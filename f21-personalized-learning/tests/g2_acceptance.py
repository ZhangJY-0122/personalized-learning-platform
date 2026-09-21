"""G2 acceptance on an isolated database. Writes never target the main demo volume."""
import concurrent.futures,hashlib,json,os,re,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[1]
PROJECT='f21-g2-check-'+uuid.uuid4().hex[:8]
ENV={**os.environ,'SERVER_PORT':'18085','WEB_PORT':'15175','WORKER_ENABLED':'false'}
COMPOSE=['docker','compose','-p',PROJECT,'-f','compose.yaml','-f','tests/compose-g1.yaml']
BASE='http://127.0.0.1:18085/api/v1'
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
        value=json.loads(response.read());assert response.status==status,(path,response.status,status,value)
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
passed=False
try:
    compose('up','-d','--no-build');ready()
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE success=1 AND version IN ('1','2','3','4');")=='4'
    student=login('student01');outsider=login('student02');teacher=login('teacher01');admin=login('admin01')
    initial=state();assert len(initial['items'])==8 and all(i['evidenceCount']==0 and i['mastery']==.2 for i in initial['items'])
    assert all(i['ruleScore']==.5 and i['ruleEvidenceCount']==0 for i in initial['items'])
    assert initial['meta']['stateRevision']==0 and not initial['meta']['stale']
    assert request('/students/'+U+'/profile?courseId='+C,student)['accuracy'] is None
    request('/practice/submissions',body=body(),key=str(uuid.uuid4()),status=401)
    request('/practice/submissions',teacher,body(),str(uuid.uuid4()),status=403)
    request('/practice/submissions',outsider,body(),str(uuid.uuid4()),status=403)
    request('/practice/submissions',student,body(),status=400)
    request('/practice/submissions',student,body(),key='bad',status=400)
    request('/practice/submissions',student,{**body(),'correct':True},str(uuid.uuid4()),status=400)
    request('/practice/submissions',student,body(version='old'),str(uuid.uuid4()),status=409)
    request('/practice/submissions',student,body(['A','A']),str(uuid.uuid4()),status=400)
    request('/practice/submissions',student,body(['A','B']),str(uuid.uuid4()),status=400)
    request('/practice/submissions',student,body('Z'),str(uuid.uuid4()),status=400)
    assert counts()==[0,0,0,0]
    sql("CREATE TRIGGER g2_fail_event BEFORE INSERT ON event_consume_log FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G2 test transaction A';",as_root=True)
    request('/practice/submissions',student,body(),str(uuid.uuid4()),status=500)
    assert counts()==[0,0,0,0] and sql('SELECT COUNT(*) FROM learner_course_state;')=='0'
    sql('DROP TRIGGER g2_fail_event;',as_root=True)
    key=str(uuid.uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        firsts=list(pool.map(lambda _:submit('B',key=key),range(8)))
    first=firsts[0];assert all(x==first for x in firsts);assert counts()==[1,1,0,0]
    assert not first['correct'];assert state()['meta']['stale']
    request('/practice/submissions',student,body('A'),key,status=409)
    request('/practice/submissions/'+first['submissionId'],outsider,status=403)
    request('/students/'+U+'/practice-history?courseId='+C,outsider,status=403)
    request('/students/'+U+'/mastery?courseId='+C,teacher,status=403)
    request('/admin/events/'+first['eventId']+'/retry',student,{},status=403)
    # Editing mutable authoring data must not silently change the published question.
    sql("UPDATE question SET stem='EDITED DRAFT',answer_json=JSON_ARRAY('B') WHERE question_id='"+Q+"';")
    displayed=request('/questions/'+Q,student);assert displayed['stem']==SCENARIO['questions'][0]['stem']
    second=submit('A');assert second['correct']
    sql("CREATE TRIGGER g2_fail_head BEFORE INSERT ON mastery_history FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G2 test queue head';",as_root=True)
    worker(True)
    wait(lambda:request('/practice/submissions/'+first['submissionId'],student)['processingStatus']=='FAILED')
    assert request('/practice/submissions/'+second['submissionId'],student)['processingStatus']=='PENDING'
    assert counts()==[2,2,0,0]
    sql('DROP TRIGGER g2_fail_head;',as_root=True)
    request('/admin/events/'+first['eventId']+'/retry',admin,{})
    wait(lambda:finished(second))
    expected=bkt(bkt(.2,False),True);assert abs(item()['mastery']-expected)<1e-12
    assert item()['evidenceCount']==2 and item()['evidenceStatus']=='INSUFFICIENT'
    assert counts()==[2,2,2,2]
    revision=state()['meta']['stateRevision'];assert revision==2
    assert submit('B',key=key)==first;assert state()['meta']['stateRevision']==revision
    # A true transaction-B fault after interaction/mastery writes must roll everything back.
    worker(False)
    sql("CREATE TRIGGER g2_fail_history BEFORE INSERT ON mastery_history FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G2 test rollback';",as_root=True)
    third=submit('B')
    worker(True)
    wait(lambda:request('/practice/submissions/'+third['submissionId'],student)['processingStatus']=='FAILED')
    assert counts()==[3,3,2,2] and item()['evidenceCount']==2
    assert state()['meta']['stale']
    # retry_count is terminal at 5 (the fifth failure becomes NEEDS_ATTENTION);
    # do not wait for an impossible sixth attempt.
    for attempt in range(2,6):
        sql("UPDATE event_consume_log SET next_retry_at='2000-01-01' WHERE event_id='"+third['eventId']+"';")
        wait(lambda:int(sql("SELECT retry_count FROM event_consume_log WHERE event_id='"+third['eventId']+"';"))>=attempt)
    assert request('/practice/submissions/'+third['submissionId'],student)['processingStatus']=='NEEDS_ATTENTION'
    assert counts()==[3,3,2,2]
    sql('DROP TRIGGER g2_fail_history;',as_root=True)
    request('/admin/events/'+third['eventId']+'/retry',admin,{})
    wait(lambda:finished(third));assert counts()==[3,3,3,3]
    expected=bkt(expected,False);assert abs(item()['mastery']-expected)<1e-12
    assert item()['evidenceCount']==3 and item()['evidenceStatus']=='SUFFICIENT'
    assert item()['ruleScore']==.4 and item()['ruleEvidenceCount']==3
    # Out-of-order trusted local event triggers a rebuild, not an incorrect append.
    worker(False);late=submit('A')
    raw=json.loads(sql("SELECT payload_json FROM event_consume_log WHERE event_id='"+late['eventId']+"';"))
    raw['occurredAt']='2020-01-01T00:00:00Z'
    encoded=json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    escaped=encoded.replace("'","''")
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    sql("UPDATE event_consume_log SET occurred_at='2020-01-01 00:00:00',payload_json='"+escaped+"',payload_hash='"+digest+"' WHERE event_id='"+late['eventId']+"';")
    worker(True)
    wait(lambda:baseline(U,C).endswith('REBUILD_REQUIRED'))
    assert counts()==[4,4,3,3]
    request('/admin/students/'+U+'/rebuild?courseId='+C,admin,{})
    assert finished(late);assert counts()==[4,4,4,7]
    expected=.2
    for correct in [True,False,True,False]:expected=bkt(expected,correct)
    assert abs(item()['mastery']-expected)<1e-12 and item()['evidenceCount']==4
    assert item()['ruleScore']==.5 and item()['ruleEvidenceCount']==4
    assert baseline(U,C).endswith(':1:READY')
    profile=request('/students/'+U+'/profile?courseId='+C,student)
    assert profile['totalAnswers']==4 and profile['accuracy']==.5 and profile['activityDays']==1
    saved=state();saved_counts=counts()
    compose('restart','mysql','server');ready()
    assert state()==saved and counts()==saved_counts
    # Recovery of committed pending work after process replacement.
    worker(False);queued=submit('A');assert not finished(queued)
    worker(True);wait(lambda:finished(queued));assert counts()==[5,5,5,8]
    # Rebuild failure retains previous derived results and exposes stale status.
    old=item()['mastery'];oldcounts=counts()
    sql("CREATE TRIGGER g2_fail_rebuild BEFORE INSERT ON mastery_history FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G2 test rebuild rollback';",as_root=True)
    request('/admin/students/'+U+'/rebuild?courseId='+C,admin,{},status=500)
    assert item()['mastery']==old and counts()==oldcounts and state()['meta']['stale']
    sql('DROP TRIGGER g2_fail_rebuild;',as_root=True)
    request('/admin/students/'+U+'/rebuild?courseId='+C,admin,{})
    assert item()['mastery']==old and not state()['meta']['stale']
    # Rolling UTC calendar window; mutate only disposable test data, then restore it.
    before_window=baseline(U,C)
    interaction_ids=sql('SELECT interaction_id FROM learning_interaction ORDER BY interaction_id;').splitlines()
    assert len(interaction_ids)==5
    for interaction_id,offset in zip(interaction_ids,[0,-6,-6,-7,1]):
        sql("UPDATE learning_interaction SET occurred_at=UTC_DATE()+INTERVAL "+str(offset)+" DAY WHERE interaction_id='"+interaction_id+"';")
    assert request('/students/'+U+'/profile?courseId='+C,student)['activityDays']==2
    sql('UPDATE learning_interaction SET occurred_at=UTC_DATE()-INTERVAL 7 DAY;')
    expired_profile=request('/students/'+U+'/profile?courseId='+C,student)
    assert expired_profile['activityDays']==0 and expired_profile['totalAnswers']==5
    assert baseline(U,C)==before_window
    sql('UPDATE learning_interaction i JOIN event_consume_log e ON e.event_id=i.source_event_id SET i.occurred_at=e.occurred_at;')
    # Restore authoring text in disposable test DB for browser acceptance.
    sql("UPDATE question q JOIN question_snapshot s ON q.question_id=s.question_id SET q.stem=s.stem,q.answer_json=s.answer_json;")
    if os.environ.get('PLAYWRIGHT_MODULE'):
        browserenv={**ENV,'G2_URL':'http://127.0.0.1:15175','G2_PROJECT':PROJECT}
        subprocess.run(['node','tests/g2-ui.cjs'],cwd=ROOT,env=browserenv,check=True)
    passed=True
    report={'passed':True,'project':PROJECT,'httpChecks':len(checks),'migrations':4,'concurrentIdempotency':True,'snapshotGrading':True,'transactionBRollback':True,'retryRecovery':True,'lateEventRebuild':True,'rebuildRollback':True,'restartConsistency':True,'pendingRecovery':True,'noFabricatedProfileFields':True,'volumeRetained':PROJECT+'_f21-data','checks':checks}
    Path(os.environ.get('G2_OUTPUT', str(ROOT/'artifacts/g2-acceptance.json'))).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('G2 ACCEPTANCE PASS:',len(checks),'HTTP checks and transactional fault tests')
finally:
    if not passed:
        print(compose('logs','--tail=40','server')[-6000:])
    compose('down')
