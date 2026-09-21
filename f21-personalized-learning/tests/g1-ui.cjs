const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const u=new URL(route.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname)){external.push(u.origin);return route.abort();}return route.continue();});
  const waitReady=()=>page.waitForFunction(()=>!document.querySelector('[role=status]'));
  async function login(name,password='Learn@12345'){await page.getByLabel('用户名').fill(name);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await waitReady();}
  async function logout(){await page.getByRole('button',{name:'退出登录'}).click();await page.getByLabel('用户名').waitFor();}
  await page.goto(process.env.G1_URL||'http://127.0.0.1:15173');
  await login('student01','wrong');await page.getByRole('alert').waitFor();
  await login('student01');await page.getByRole('heading',{name:'我的课程'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'进入课程'}).count(),1);
  await page.getByRole('button',{name:'进入课程'}).click();await waitReady();
  await page.getByText('8 个知识点 · 7 条先修关系').waitFor();
  await page.screenshot({path:path.join(__dirname,'../artifacts/g1-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'资源',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'阅读资源'}).count(),12);
  await page.getByRole('button',{name:'阅读资源'}).first().click();await page.locator('.reader').waitFor();
  assert.ok((await page.locator('.reader').innerText()).includes('int count'));
  await page.getByRole('button',{name:'题目',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'查看题目'}).count(),40);
  await page.getByRole('button',{name:'查看题目'}).first().click();await page.locator('.reader').waitFor();
  assert.ok((await page.locator('.reader').innerText()).includes('A. 3'));
  await page.reload();await page.getByRole('heading',{name:'我的课程'}).waitFor();await logout();
  for(const name of ['student02','teacher02']){await login(name);await page.getByText('暂无已授权课程，请联系管理员分配课程。').waitFor();await logout();}
  await login('teacher01');assert.equal(await page.getByRole('button',{name:'进入课程'}).count(),1);await logout();
  await login('admin01');assert.equal(await page.getByRole('button',{name:'进入课程'}).count(),2);await logout();
  await page.setViewportSize({width:390,height:844});await login('student01');
  await page.getByRole('button',{name:'进入课程'}).click();await waitReady();
  await page.getByRole('button',{name:'资源',exact:true}).click();await page.getByRole('button',{name:'阅读资源'}).first().click();await page.locator('.reader').waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(__dirname,'../artifacts/g1-mobile.png'),fullPage:true});
  await logout();assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(__dirname,'../artifacts/g1-ui.json'),JSON.stringify({passed:true,roles:3,unauthorizedAccounts:2,resources:12,questions:40,sessionRefresh:true,mobileWidth:390,pageErrors:errors,externalRequests:external},null,2)+'\n');
  console.log('G1 UI PASS: real logins, course, offline resources, questions, reload, logout, empty scopes, mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
