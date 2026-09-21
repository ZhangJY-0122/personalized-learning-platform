"""Isolated fresh-volume + restart verification. Never deletes any volume."""
import json,os,subprocess,sys,time,uuid
from pathlib import Path
import g1_api as api
ROOT=Path(__file__).resolve().parents[1]
PROJECT='f21-g1-check-'+uuid.uuid4().hex[:8]
ENV={**os.environ,'SERVER_PORT':'18084','WEB_PORT':'15174'}
COMPOSE=['docker','compose','-p',PROJECT,'-f','compose.yaml','-f','tests/compose-g1.yaml']
api.BASE='http://127.0.0.1:18084/api/v1'
def compose(*args):
    return subprocess.run(COMPOSE+list(args),cwd=ROOT,env=ENV,check=True,capture_output=True,text=True).stdout
def sql(statement):
    return subprocess.run(COMPOSE+['exec','-T','mysql','sh','-c','MYSQL_PWD="$MYSQL_PASSWORD" mysql -u "$MYSQL_USER" "$MYSQL_DATABASE" -N -B'],input=statement,cwd=ROOT,env=ENV,check=True,capture_output=True,text=True).stdout.strip()
def ready():
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        try:
            if api.request('/health')['stage'] in ('G3_RECOMMENDATIONS','G4_PATHS'):return
        except Exception:pass
        time.sleep(2)
    raise RuntimeError('Fresh server did not become healthy')
try:
    compose('up','-d','--no-build')
    ready()
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE success=1 AND version IN ('1','2','3');")=='3'
    assert sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='prep_catalog';")=='0'
    assert sql("SELECT COUNT(*) FROM local_user WHERE password_hash LIKE '$2a$12$%' OR password_hash LIKE '$2b$12$%';")=='5'
    subprocess.run([sys.executable,str(ROOT/'tests/g1_api.py'),'--base',api.BASE,'--output','artifacts/g1-fresh-api.json'],cwd=ROOT,check=True)
    token=api.login('student01');teacher=api.login('teacher01')
    rid=api.IDS['resourceId'];sid=api.IDS['studentId'];cid=api.IDS['courseId']
    marker='G1 persistence '+uuid.uuid4().hex
    sql("UPDATE resource SET content_text=CONCAT(content_text,' "+marker+"') WHERE resource_id='"+rid+"';")
    before=sql("SELECT username,password_hash FROM local_user ORDER BY username;")
    api.request('/auth/logout',token,body={})
    sql("UPDATE teacher_course_scope SET status='INACTIVE' WHERE course_id='"+cid+"';")
    api.request('/courses/'+cid+'/structure',teacher,expected=403)
    sql("UPDATE teacher_course_scope SET status='ACTIVE' WHERE course_id='"+cid+"';")
    token2=api.login('student01')
    sql("UPDATE local_user SET status='DISABLED' WHERE user_id='"+sid+"';")
    api.request('/users/me',token2,expected=401)
    api.request('/auth/login',body={'username':'student01','password':'Learn@12345'},expected=401)
    sql("UPDATE local_user SET status='ACTIVE' WHERE user_id='"+sid+"';")
    sql("UPDATE course SET status='INACTIVE' WHERE course_id='"+cid+"';")
    api.request('/resources/'+rid,token2,expected=404)
    assert api.request('/courses',token2)['total']==0
    sql("UPDATE course SET status='ACTIVE' WHERE course_id='"+cid+"';")
    compose('restart','mysql','server')
    ready()
    assert marker not in api.request('/resources/'+rid,token2)['content']
    assert marker in sql("SELECT content_text FROM resource WHERE resource_id='"+rid+"';")
    api.request('/users/me',token,expected=401)
    assert before==sql("SELECT username,password_hash FROM local_user ORDER BY username;")
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE success=1 AND version IN ('1','2','3');")=='3'
    compose('up','-d','--no-build','--no-deps','--force-recreate','server')
    api.BASE='http://127.0.0.1:15174/api/v1'
    ready()
    assert marker not in api.request('/resources/'+rid,token2)['content']
    assert marker in sql("SELECT content_text FROM resource WHERE resource_id='"+rid+"';")
    report={'passed':True,'project':PROJECT,'freshVolume':True,'migrations':3,'bcryptAccounts':5,'resourceEditSurvivedRestart':True,'passwordHashesUnchanged':True,'revocationSurvivedRestart':True,'disabledAccountRejected':True,'scopeRevocationImmediate':True,'inactiveCourseHidden':True,'proxyAfterServerRecreate':True,'volumeRetained':PROJECT+'_f21-data'}
    Path(os.environ.get('G1_OUTPUT', str(ROOT/'artifacts/g1-persistence.json'))).write_text(json.dumps(report,indent=2)+'\n')
    print('G1 fresh-volume and restart PASS')
finally:
    # Remove only these disposable validation containers/network; retain their data volume.
    compose('down')
