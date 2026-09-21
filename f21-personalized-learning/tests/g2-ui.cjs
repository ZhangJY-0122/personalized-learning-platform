const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const u=new URL(route.request().url());if(!['localhost','127.0.0.1'].includes(u.hostname)){external.push(u.origin);return route.abort();}return route.continue();});
  await page.goto(process.env.G2_URL||'http://127.0.0.1:15173');
  await page.getByLabel('用户名').fill('student01');await page.getByLabel('密码',{exact:true}).fill('Learn@12345');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('heading',{name:'我的课程'}).waitFor();
  async function enter(){await page.getByRole('button',{name:'进入课程'}).first().click();await page.getByText('8 个知识点 · 7 条先修关系').waitFor();}
  async function tab(name){await page.getByRole('button',{name,exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[role=status]'));}
  async function total(){await tab('历史');return Number((await page.getByText(/共 \d+ 条，每页/).innerText()).match(/共 (\d+) 条/)[1]);}
  async function openQuestion(){await tab('题目');await page.getByRole('button',{name:'查看题目'}).first().click();await page.getByRole('group',{name:'选择答案'}).waitFor();}
  await enter();const before=await total();await openQuestion();
  assert.equal(await page.getByRole('button',{name:'提交答案',exact:true}).isDisabled(),true);
  await page.getByLabel('B. -1',{exact:true}).check();
  await page.getByRole('button',{name:'提交答案',exact:true}).click();await page.getByText('回答错误',{exact:true}).waitFor();
  await page.getByText('学习状态已更新。',{exact:true}).waitFor({timeout:20000});
  assert.equal(await total(),before+1);
  await tab('掌握度');await page.getByRole('heading',{name:'我的学习状态'}).waitFor();
  assert.equal(await page.locator('progress').count(),8);
  assert.ok((await page.locator('table').innerText()).includes('证据不足'));
  await page.screenshot({path:path.join(__dirname,'../artifacts/g2-mastery.png'),fullPage:true});
  // Lose a successful POST response, reload, and retry the original durable browser intent.
  await openQuestion();await page.getByRole('button',{name:'再练一次'}).click();await page.getByLabel('A. 3',{exact:true}).check();
  const keys=[];let drop=true;
  await page.route('**/api/v1/practice/submissions',async route=>{
   keys.push(route.request().headers()['idempotency-key']);
   if(drop){drop=false;const response=await route.fetch();assert.equal(response.status(),200);await route.abort('failed');}
   else await route.continue();
  });
  await page.getByRole('button',{name:'提交答案',exact:true}).click();
  await page.getByText(/请求结果未确认。已保留原答案/).waitFor({timeout:20000});
  await page.reload();await page.getByRole('heading',{name:'我的课程'}).waitFor();await enter();await openQuestion();
  await page.getByRole('button',{name:'重试原请求',exact:true}).click();
  await page.getByText('回答正确',{exact:true}).waitFor();await page.getByText('学习状态已更新。',{exact:true}).waitFor({timeout:20000});
  assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);assert.equal(await total(),before+2);
  await page.screenshot({path:path.join(__dirname,'../artifacts/g2-history.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.getByRole('button',{name:'退出登录'}).click();await page.getByLabel('用户名').waitFor();
  fs.writeFileSync(path.join(__dirname,'../artifacts/g2-ui.json'),JSON.stringify({passed:true,desktopViewport:'1440x1000',baselineSubmissions:before,finalSubmissions:before+2,serverGrading:true,masteryAndHistory:true,lostResponseRetrySameKey:true,reloadRestoresPendingIntent:true,noDuplicateSubmission:true,pageErrors:errors,externalRequests:external},null,2)+'\n');
  console.log('G2 desktop browser PASS, including lost-response/reload/idempotent retry');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
