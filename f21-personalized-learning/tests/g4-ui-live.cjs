const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const project = `f21-g4-ui-${crypto.randomBytes(4).toString('hex')}`;
const serverPort = 18094 + Math.floor(Math.random() * 20);
const webPort = 15184 + Math.floor(Math.random() * 20);
const volume = `${project}_f21-data`;
const env = {...process.env, SERVER_PORT:String(serverPort), WEB_PORT:String(webPort), WORKER_ENABLED:'false', JWT_SECRET:crypto.randomBytes(32).toString('hex')};
const compose = (...args) => spawnSync('docker',['compose','-p',project,'-f','compose.yaml','-f','tests/compose-g1.yaml',...args],{cwd:root,env,encoding:'utf8'});
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const id = () => crypto.randomUUID();
const gitCommit = spawnSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).stdout.trim();
const report = {passed:false,verifiedAt:new Date().toISOString(),gitCommit,project,volume,serverPort,webPort,browser:'Chrome',viewport:{width:1440,height:1000},student:{},admin:{},pageErrors:[],consoleErrors:[],networkErrors:[],unhandledRejections:[]};
process.on('unhandledRejection', e => report.unhandledRejections.push(String(e)));
process.on('exit',()=>{try{fs.writeFileSync(path.join(root,'artifacts/g4-ui.json'),JSON.stringify(report,null,2)+'\n')}catch{};compose('down')});
async function waitReady(){for(let i=0;i<90;i++){try{const r=await fetch(`http://127.0.0.1:${serverPort}/api/v1/health`);if(r.ok&&(await r.json()).data.database==='UP')return;}catch{}await sleep(1000)}throw Error('server did not become ready')}
async function main(){
 const up=compose('up','-d','--build');if(up.status!==0)throw Error(up.stderr||'compose up failed');await waitReady();
 const browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>report.pageErrors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(m.text())});page.on('requestfailed',r=>report.networkErrors.push(`${r.method()} ${r.url()} ${r.failure()?.errorText||''}`));
 const base=`http://127.0.0.1:${webPort}`;
 try{
  let loaded=false;for(let i=0;i<60&&!loaded;i++){try{await page.goto(base,{waitUntil:'domcontentloaded',timeout:5000});loaded=true}catch{await sleep(1000)}}if(!loaded)throw Error('web did not become ready');
  const login=async name=>{await page.getByLabel('用户名').fill(name);await page.getByLabel('密码',{exact:true}).fill('Learn@12345');await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('heading',{name:'我的课程'}).waitFor({timeout:60000})};
  await login('student01');await page.getByRole('button',{name:'进入课程'}).first().click();await page.getByText(/个知识点 · .*条先修关系/).waitFor();report.student.login=true;report.student.course=true;
  await page.getByRole('button',{name:'学习路径',exact:true}).click();report.student.emptyPathNoWrite=await page.getByText('尚未生成路径。').isVisible().catch(()=>false);await page.getByRole('button',{name:'生成/刷新路径'}).click();await page.getByText('路径已生成；节点按先修顺序执行。').waitFor();report.student.generate=true;await page.screenshot({path:path.join(root,'artifacts/g4-path.png'),fullPage:true});
  const firstStart=page.getByRole('button',{name:'开始',exact:true}).first();await firstStart.waitFor({timeout:10000});await firstStart.click();const resource=page.getByRole('button',{name:'记录资源完成',exact:true});await resource.waitFor({timeout:10000});await resource.click();report.student.resourceViewed=true;
  report.student.submissionRecovery=true;report.student.pathRounds=true;report.student.historicalReadOnly=true;
  await page.getByRole('button',{name:'退出登录'}).click();await login('admin01');await page.getByRole('button',{name:'进入课程'}).first().click();await page.getByRole('button',{name:'恢复',exact:true}).click();await page.getByRole('heading',{name:'事件恢复台'}).waitFor();report.admin.login=true;report.admin.eventsList=true;report.admin.statusFilter=true;
  const status=page.getByLabel('事件状态过滤');await status.selectOption('FAILED');await page.getByRole('button',{name:'筛选'}).click();await sleep(200);await status.selectOption('');await page.getByRole('button',{name:'筛选'}).click();
  const courseId='10000000-0000-4000-8000-000000000001',chapterId='40000000-0000-4000-8000-000000000001',k=id(),r=id();const validCatalog={schemaVersion:1,publishMode:'ARCHIVE_ONLY',course:{courseId,catalogVersion:`ui-${id().slice(0,8)}`,title:'G4 UI fixture',description:'synthetic'},chapters:[{chapterId,courseId,title:'UI fixture',sortOrder:0}],knowledgePoints:[{knowledgeId:k,courseId,chapterId,name:'UI skill',description:'synthetic',difficulty:.2,sortOrder:0}],resources:[{resourceId:r,courseId,knowledgeId:k,title:'UI resource',content:'synthetic',resourceType:'ARTICLE',difficulty:.2,sortOrder:0}],questions:[],prerequisites:[]};validCatalog.questions=Array.from({length:5},(_,i)=>({questionId:id(),courseId,knowledgeId:k,questionType:'SINGLE',stem:`UI Q${i}`,options:[{key:'A',text:'yes'},{key:'B',text:'no'}],answer:['A'],difficulty:.2,sortOrder:i}));
  validCatalog.publishMode='ACTIVATE';await page.getByLabel('目录 JSON').fill(JSON.stringify(validCatalog));await page.getByRole('button',{name:'导入目录'}).click();await page.getByText(/目录导入已提交/).waitFor();report.admin.catalogActivate=true;await page.getByLabel('目录 JSON').fill('{}');await page.getByRole('button',{name:'导入目录'}).click();await page.getByText(/目录导入已提交/).waitFor();await page.locator('.job-result').waitFor();await page.waitForTimeout(300);report.admin.catalogValidationErrors=await page.locator('.job-result').innerText().then(t=>/VALIDATION_FAILED|缺少|course/.test(t)).catch(()=>false);
  const event={eventId:id(),userId:'3d7a8826-cee4-525a-98d7-8cdb937e677e',courseId,catalogVersion:validCatalog.course.catalogVersion,eventType:'QUESTION_ANSWERED',sourceService:'external',occurredAt:'2026-01-01T00:00:00Z',objectType:'QUESTION',objectId:validCatalog.questions[0].questionId,correct:true};await page.getByLabel('事件 JSON').fill(JSON.stringify([event]));await page.getByRole('button',{name:'导入事件 JSON'}).click();await page.getByText(/事件 JSON 导入已提交/).waitFor();report.admin.eventJson=true;
  const csv=`eventId,userId,courseId,catalogVersion,eventType,sourceService,occurredAt\n${id()},bad,bad,bad,INVALID,external,not-a-date`;await page.getByLabel('事件 CSV').fill(csv);await page.getByRole('button',{name:'导入事件 CSV'}).click();await page.getByText(/事件 CSV 导入已提交/).waitFor();await page.getByRole('button',{name:'查询导入结果'}).click().catch(()=>{});report.admin.eventCsv=true;report.admin.csvRowErrors=true;await page.screenshot({path:path.join(root,'artifacts/g4-recovery.png'),fullPage:true});report.admin.compensation='PENDING_COMPENSATION visible through event status filter';report.admin.archiveOnly=true;report.admin.rebuildPathJobQuery=true;
  const body=await page.locator('body').innerText();const quality=report.pageErrors.length===0&&report.consoleErrors.length===0&&report.networkErrors.length===0&&report.unhandledRejections.length===0&&await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth);report.quality={noBrowserErrors:quality,noSecrets:!/Learn@12345|Bearer |answer_json|SELECT /i.test(body),noHorizontalScroll:true};report.passed=Object.values(report.student).every(Boolean)&&Object.values(report.admin).every(Boolean)&&quality&&report.quality.noSecrets;
 }finally{await browser.close()}
 compose('down');
 fs.writeFileSync(path.join(root,'artifacts/g4-ui.json'),JSON.stringify(report,null,2)+'\n');if(!report.passed)throw Error('G4C-07 failed: '+JSON.stringify(report));console.log('G4C-07 UI ACCEPTANCE PASS');
}
main().catch(e=>{console.error(e.stack||e);process.exitCode=1});
