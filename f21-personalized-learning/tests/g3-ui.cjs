const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const u=new URL(route.request().url());if(!['localhost','127.0.0.1'].includes(u.hostname)){external.push(u.origin);return route.abort();}return route.continue();});
  await page.goto(process.env.G3_URL||'http://127.0.0.1:15176');
  await page.getByLabel('用户名').fill('student01');await page.getByLabel('密码',{exact:true}).fill('Learn@12345');await page.getByRole('button',{name:'登录',exact:true}).click();
  async function enter(){await page.getByRole('button',{name:'进入课程'}).first().click();await page.getByText('8 个知识点 · 7 条先修关系').waitFor();}
  async function tab(name){await page.getByRole('button',{name,exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[role=status]'));}
  await enter();await tab('推荐');await page.getByRole('heading',{name:'我的个性化推荐'}).waitFor();
  const keys=[];let drop=true;
  await page.route('**/recommendations/generate',async route=>{
   keys.push(route.request().headers()['idempotency-key']);
   if(drop){drop=false;const r=await route.fetch();assert.equal(r.status(),200);await route.abort('failed');}else await route.continue();
  });
  await page.getByRole('button',{name:'生成推荐',exact:true}).click();await page.getByRole('alert').filter({hasText:'重试会沿用原请求编号'}).waitFor();
  await page.reload();await enter();await tab('推荐');await page.getByRole('button',{name:'生成推荐',exact:true}).click();
  await page.getByText('推荐已保存；再次生成将创建新批次。',{exact:true}).waitFor();assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);
  await page.unroute('**/recommendations/generate');
  let first=page.locator('[data-recommendation-id]').first();
  await first.getByRole('button',{name:'自报已完成',exact:true}).click();await page.getByText(/已记录自报完成/).waitFor();
  const feedbackKeys=[];let loseFeedback=true;
  await page.route('**/recommendations/*/feedback',async route=>{
   if(route.request().postDataJSON().feedbackType!=='HELPFUL')return route.continue();
   feedbackKeys.push(route.request().headers()['idempotency-key']);
   if(loseFeedback){loseFeedback=false;assert.equal((await route.fetch()).status(),200);return route.abort('failed');}return route.continue();
  });
  await first.getByRole('button',{name:'有帮助',exact:true}).click();await page.getByRole('alert').filter({hasText:'重试会沿用原请求编号'}).waitFor();
  await first.getByRole('button',{name:'有帮助',exact:true}).click();await first.getByText('我的意见：有帮助',{exact:true}).waitFor();
  assert.equal(feedbackKeys.length,2);assert.equal(feedbackKeys[0],feedbackKeys[1]);await page.unroute('**/recommendations/*/feedback');
  const resource=page.getByRole('button',{name:'打开推荐资源',exact:true}).first();
  assert.equal(await resource.count(),1,'Resource branch must run');
  {
   // A lost resource view response is recovered before completing; never generate a second view intent.
   const viewKeys=[],completionKeys=[],clicks=[];let loseView=true,loseCompletion=true;
   const resourceRec=await resource.locator('..').getAttribute('data-recommendation-id');
   page.on('request',request=>{if(request.url().includes('/recommendations/'+resourceRec+'/feedback')&&request.postDataJSON()?.feedbackType==='CLICKED')clicks.push(request);});
   await page.route('**/resources/*/views',async route=>{viewKeys.push(route.request().headers()['idempotency-key']);if(loseView){loseView=false;assert.equal((await route.fetch()).status(),200);await route.abort('failed');}else await route.continue();});
   await resource.click();await page.getByRole('alert').filter({hasText:'重试会沿用原请求编号'}).waitFor();
   assert.equal(clicks.length,1,'Visible content must record a click even if the view response is lost');
   await page.route('**/resources/*/completions',async route=>{completionKeys.push(route.request().headers()['idempotency-key']);if(loseCompletion){loseCompletion=false;assert.equal((await route.fetch()).status(),200);return route.abort('failed');}return route.continue();});
   await page.getByRole('button',{name:'记录资源完成',exact:true}).click();await page.getByRole('alert').filter({hasText:'重试会沿用原请求编号'}).waitFor();
   await page.getByRole('button',{name:'记录资源完成',exact:true}).click();await page.getByRole('button',{name:'已记录完成',exact:true}).waitFor();
   assert.equal(viewKeys.length,2);assert.equal(viewKeys[0],viewKeys[1]);await page.unroute('**/resources/*/views');
   assert.equal(completionKeys.length,2);assert.equal(completionKeys[0],completionKeys[1]);await page.unroute('**/resources/*/completions');
  }
  await tab('推荐');await page.getByRole('button',{name:'练习推荐题目',exact:true}).first().click();await page.getByRole('group',{name:'选择答案'}).waitFor();
  await page.getByRole('radio').first().check();await page.getByRole('button',{name:'提交答案',exact:true}).click();await page.getByText('学习状态已更新。',{exact:true}).waitFor({timeout:20000});
  await tab('推荐');await page.getByText('已关联服务端确认的完成记录',{exact:true}).first().waitFor();
  await page.getByRole('alert').filter({hasText:'旧推荐'}).waitFor();
  await page.screenshot({path:path.join(__dirname,'../artifacts/g3-recommendations.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  fs.writeFileSync(path.join(__dirname,'../artifacts/g3-ui.json'),JSON.stringify({passed:true,verifiedAt:new Date().toISOString(),desktopViewport:'1440x1000',lostGenerateResponseSameKey:true,lostFeedbackResponseSameKey:true,lostResourceViewSameKey:true,lostResourceCompletionSameKey:true,clickRecordedDespiteLostView:true,questionTrustedAttribution:true,staleVisible:true,pageErrors:errors,externalRequests:external},null,2)+'\n');
  console.log('G3 desktop browser PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
