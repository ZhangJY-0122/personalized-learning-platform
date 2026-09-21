"""G1 live API acceptance. Local demo accounts only; report never includes tokens."""
import argparse,base64,hashlib,hmac,json,time,uuid,re
from jsonschema import Draft202012Validator,FormatChecker
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
IDS={"courseId":"10000000-0000-4000-8000-000000000001","studentId":"3d7a8826-cee4-525a-98d7-8cdb937e677e","privateCourseId":"10000000-0000-4000-8000-000000000002","resourceId":"15ed9295-11ee-552e-8153-f797788d0edf","questionId":"fd921ceb-bb08-5062-a3b7-9f91e80aa589","hiddenResourceId":"50000000-0000-4000-8000-000000000099"}
results=[]
SPEC=json.loads((ROOT/'docs/openapi.json').read_text())
def request(path,token=None,body=None,expected=200,method=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    req=Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    try: response=urlopen(req,timeout=15)
    except HTTPError as e: response=e
    with response:
        status=response.status
        data=json.loads(response.read())
        assert status==expected,(path,status,expected,data.get('message'))
        uuid.UUID(data['traceId'])
        assert response.headers.get('X-Trace-Id')==data['traceId'],(path,'trace mismatch')
        assert 'no-store' in response.headers.get('Cache-Control','')
        for template,operations in SPEC['paths'].items():
            pattern=re.sub(r'\{[^}]+\}',r'[^/]+',template)
            if re.fullmatch(pattern,path.split('?')[0]):
                operation=operations.get(req.get_method().lower())
                if operation:
                    response_schema=operation['responses'].get(str(status))
                    if response_schema:
                        schema=response_schema['content']['application/json']['schema']
                        Draft202012Validator({**schema,'components':SPEC['components']},format_checker=FormatChecker()).validate(data)
    results.append({'path':path,'status':status,'passed':True})
    return data.get('data',data)
def login(name):
    return request('/auth/login',body={'username':name,'password':'Learn@12345'})['accessToken']
def no_answers(value):
    if isinstance(value,dict):
        assert not any('answer' in k.lower() or 'password' in k.lower() for k in value),value.keys()
        for v in value.values():no_answers(v)
    elif isinstance(value,list):
        for v in value:no_answers(v)
def sign(claims):
    secret=next(x.split('=',1)[1] for x in (ROOT/'.env').read_text().splitlines() if x.startswith('JWT_SECRET=')).encode()
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x,separators=(',',':')).encode()).rstrip(b'=')
    unsigned=enc({'alg':'HS256','typ':'JWT'})+b'.'+enc(claims)
    return (unsigned+b'.'+base64.urlsafe_b64encode(hmac.new(secret,unsigned,hashlib.sha256).digest()).rstrip(b'=')).decode()
def main():
    health=request('/health');assert health['stage'] in ('G3_RECOMMENDATIONS','G4_PATHS')
    request('/courses',expected=401)
    request('/auth/login',body={'username':'student01','password':'wrong'},expected=401)
    request('/auth/login',body={'username':"' OR 1=1 --",'password':'wrong'},expected=401)
    request('/auth/login',body={'username':'student01','password':'Learn@12345','role':'ADMIN'},expected=400)
    request('/auth/login',body={'username':'','password':''},expected=400)
    tokens={name:login(name) for name in ['student01','student02','teacher01','teacher02','admin01']}
    c=IDS['courseId']
    for name,t in tokens.items():
        me=request('/users/me',t);no_answers(me);assert me['username']==name
        listed=request('/courses',t)
        assert listed['total']==(2 if name=='admin01' else 0 if name.endswith('02') else 1)
        if name.endswith('02'):
            for path in ['/courses/'+c+'/structure','/courses/'+c+'/resources','/courses/'+c+'/questions','/resources/'+IDS['resourceId'],'/questions/'+IDS['questionId']]:
                request(path,t,expected=403)
        else:
            structure=request('/courses/'+c+'/structure',t)
            assert len(structure['chapters'])==3 and len(structure['knowledgePoints'])==8
            assert len(structure['prerequisites'])==7
            resources=request('/courses/'+c+'/resources',t)['items'];assert len(resources)==12
            questions=request('/courses/'+c+'/questions',t)['items'];assert len(questions)==40;no_answers(questions)
            resource=request('/resources/'+IDS['resourceId'],t);assert 'for' in resource['content']
            question=request('/questions/'+IDS['questionId'],t);no_answers(question);assert len(question['options'])==2
    t=tokens['student01']
    for name in ['student01','teacher01']:
        request('/courses/'+IDS['privateCourseId']+'/structure',tokens[name],expected=403)
    request('/resources/'+IDS['hiddenResourceId'],t,expected=404)
    request('/questions/not-a-uuid',t,expected=400)
    request('/courses?pageSize=101',t,expected=400)
    request('/courses?page=0',t,expected=400)
    assert request('/courses?page=2',t)['items']==[]
    request('/students/'+IDS['studentId']+'/enrollments',t)
    request('/students/'+IDS['studentId']+'/enrollments',tokens['student02'],expected=403)
    request('/students/'+IDS['studentId']+'/enrollments',tokens['teacher01'],expected=403)
    request('/students/'+IDS['studentId']+'/enrollments',tokens['admin01'])
    request('/users/me',t[:-12]+'AAAAAAAAAAAA',expected=401)
    claims={'iss':'f21-local','aud':['f21-services'],'sub':IDS['studentId'],'role':'STUDENT','iat':int(time.time()),'exp':int(time.time())+3600,'jti':str(uuid.uuid4())}
    for patch in [{'exp':int(time.time())-120},{'iss':'wrong'},{'aud':['wrong']},{'role':'ADMIN'},{'sub':str(uuid.uuid4())},{'exp':None}]:
        request('/users/me',sign({**claims,**patch}),expected=401)
    request('/auth/logout',t,body={})
    request('/users/me',t,expected=401)
    report={'stage':'G1','baseUrl':BASE,'passed':True,'checks':len(results),'results':results}
    output=ROOT/ARGS.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('G1 API PASS:',len(results),'checks')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:18083/api/v1');parser.add_argument('--output',default='artifacts/g1-api.json');ARGS=parser.parse_args();BASE=ARGS.base;main()
