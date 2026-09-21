// Historical pre-G1 mock-page test; current real UI uses g1-ui.cjs.
// Set PLAYWRIGHT_MODULE to an installed Playwright module path when not locally installed.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
 const browser = await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page = await browser.newPage({viewport:{width:1100,height:800}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:15173');
  await page.getByRole('heading',{name:'独立学习项目 · 流程检查'}).waitFor();
  let checked=0;
  for(const name of ['练习','掌握度','推荐','路径','实验']) {
   await page.getByRole('button',{name,exact:true}).click();
   for(const state of ['正常','无数据','处理中','失败']) {
    await page.getByRole('combobox').selectOption(state);
    assert.ok((await page.locator('.flow').innerText()).length>5);checked++;
   }
  }
  const health=await page.request.get('http://127.0.0.1:15173/api/v1/health');
  assert.equal(health.status(),200);assert.equal((await health.json()).data.database,'UP');
  assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(__dirname,'../artifacts/wireframe.png'),fullPage:true});
  fs.writeFileSync(path.join(__dirname,'../artifacts/ui-smoke.json'),JSON.stringify({statesChecked:checked,pageErrors:errors,proxyDatabaseHealth:'UP'},null,2)+'\n');
  console.log('20 page states and real proxied database health passed');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
