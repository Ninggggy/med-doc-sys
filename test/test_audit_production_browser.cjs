// 完整生产构建连接隔离控制器/SQLite，不替换页面生命周期或业务方法。
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const base = process.env.AUDIT_BROWSER_URL || 'http://127.0.0.1:18767';
const output = process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output, {recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const report={mode:'production_build_isolated_backend',errors:[],badRequests:[],checks:[]};
 page.on('pageerror', e=>report.errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)report.badRequests.push({url:r.url(),status:r.status()});});
 const write=()=>fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));
 try {
  const ready=await (await page.request.get(base+'/audit-ready')).json();
  report.project_id=ready.project_id;
  await page.goto(base+'/#/filing-change-review/session/'+ready.project_id);
  await page.waitForFunction(()=>!!document.querySelector('.page-shell')?.__vue__?.loadBaseData);
  await page.evaluate(()=>{window.auditPage=document.querySelector('.page-shell').__vue__;});
  await page.waitForFunction(()=>Object.keys(auditPage.renderFields).length>0 && auditPage.project.project_name);
  await page.screenshot({path:path.join(output,'production-loaded.png'),fullPage:true});
  report.checks.push('真实生产页面及申请表、项目数据加载');
  if (!await page.evaluate(()=>auditPage.formVisible)) await page.getByRole('button',{name:'填写申请表',exact:true}).click();
  const field=page.locator('.el-collapse-item').filter({has:page.locator('.el-collapse-item__header').filter({hasText:'药品通用名称'})}).first();
  await field.locator('.el-collapse-item__header').click();
  const input=field.locator('input.el-input__inner').first();
  await input.fill('合成保存前名称');
  let release, saving;
  const pending=new Promise(r=>{saving=r;});
  const hold=new Promise(r=>{release=r;});
  await page.route('**/application-form/save',async route=>{
    const response=await route.fetch();saving();await hold;await route.fulfill({response});
  });
  await page.getByRole('button',{name:'保存申请表',exact:true}).click();
  await pending;
  await input.fill('合成保存期间新名称');
  release();
  await page.waitForFunction(()=>!auditPage.savingForm);
  assert.equal(await input.inputValue(),'合成保存期间新名称');
  await page.unroute('**/application-form/save');
  await page.getByRole('button',{name:'保存申请表',exact:true}).click();
  await page.waitForFunction(()=>!auditPage.savingForm);
  await page.reload();
  await page.waitForFunction(()=>!!document.querySelector('.page-shell')?.__vue__?.formSchema?.item_6_generic_name);
  await page.evaluate(()=>{window.auditPage=document.querySelector('.page-shell').__vue__;});
  await page.waitForFunction(()=>auditPage.formSchema.item_6_generic_name.value==='合成保存期间新名称');
  report.checks.push('生产页面保存期间继续输入保留、再次保存后刷新恢复');
  if (!await page.evaluate(()=>auditPage.formVisible)) await page.getByRole('button',{name:'填写申请表',exact:true}).click();
  await field.locator('.el-collapse-item__header').click();
  await input.fill('合成保存失败保留');
  await page.route('**/application-form/save',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'合成服务繁忙'})}));
  await page.getByRole('button',{name:'保存申请表',exact:true}).click();
  await page.waitForFunction(()=>!auditPage.savingForm);
  assert.equal(await input.inputValue(),'合成保存失败保留');
  await page.evaluate(()=>{auditPage.$router.push('/filing-change-review').catch(()=>{});});
  await page.locator('.el-message-box').getByRole('button',{name:'取消',exact:true}).click();
  assert.ok(page.url().includes(ready.project_id));
  assert.equal(await input.inputValue(),'合成保存失败保留');
  await page.unroute('**/application-form/save');
  await page.getByRole('button',{name:'保存申请表',exact:true}).click();
  await page.waitForFunction(()=>!auditPage.savingForm);
  report.checks.push('生产页面注入503保存失败保留输入、未保存离开确认且取消后保留');
  await page.getByRole('tab',{name:'申报资料',exact:true}).click();
  await page.locator('.submission-catalog .el-tree-node__content').first().click();
  await page.getByPlaceholder('当前目录不适用原因（可选）').click();
  const category=await page.evaluate(()=>auditPage.selectedSubmissionCategory);
  assert.ok(category);
  await page.evaluate(()=>auditPage.loadSubmissionCatalog());
  assert.equal(await page.evaluate(()=>auditPage.selectedSubmissionCategory),category);
  assert.equal(await page.locator('.submission-catalog .is-current').count(),1);
  report.checks.push('目录选择与服务刷新后保持');
  // 仅注入合成诊断数据；仍使用完整生产组件的分组、渲染和分页。
  await page.evaluate(()=>{
   auditPage.$set(auditPage.filingParseTaskStates,'synthetic',{status:'completed',result:{content_status:'partial',data:{parse_diagnostics:{
    failed_pages:Array.from({length:100},(_,i)=>i+1),
    errors:Array.from({length:300},(_,i)=>({page:i%100+1,stage:'ocr_quality',code:'ocr_response',bbox_pdf:[i,0,i+1,1]}))
   }}}});
  });
  const summary=page.locator('.parse-summary').first();
  assert.ok((await summary.boundingBox()).height<100);
  await summary.getByRole('button',{name:'查看详情',exact:true}).click();
  await page.locator('.parse-detail-group summary').click();
  assert.equal(await page.locator('.parse-region-list > div').count(),300);
  await page.screenshot({path:path.join(output,'diagnostics-300.png'),fullPage:true});
  const perf=await page.evaluate(async()=>{
   const measure=async(fn)=>{const s=performance.now();fn();await auditPage.$nextTick();await new Promise(requestAnimationFrame);return performance.now()-s;};
   const out={expand:[],pagination:[],directory:[]};
   for(let i=0;i<20;i++)out.expand.push(await measure(()=>document.querySelector('.parse-detail-group summary').click()));
   auditPage.openParseDetails({status:'completed',result:{data:{success:Array.from({length:45},(_,i)=>({doc_id:String(i),content_status:'success'}))}}},'合成分页');
   await auditPage.$nextTick();
   for(let i=0;i<20;i++)out.pagination.push(await measure(()=>document.querySelectorAll('.parse-detail-body .el-pagination .number')[i%3].click()));
   auditPage.parseDetailsVisible=false;
   await auditPage.$nextTick();
   const nodes=document.querySelectorAll('.submission-catalog .el-tree-node__content');
   for(let i=0;i<20;i++)out.directory.push(await measure(()=>nodes[i%Math.min(nodes.length,3)].click()));
   return out;
  });
  report.performance={metric:'事件触发至Vue nextTick及下一渲染帧，毫秒',samples:perf,p95:{}};
  for(const [name,values] of Object.entries(perf)){
   report.performance.p95[name]=[...values].sort((a,b)=>a-b)[18];
   assert.ok(report.performance.p95[name]<=500,name+' P95 >500ms');
  }
  report.checks.push('100页300条明细完整、每页20组；三类操作各20次P95');
  await page.evaluate(()=>{auditPage.$delete(auditPage.filingParseTaskStates,'synthetic');});
  assert.deepEqual(report.errors,[]);
  assert.deepEqual(report.badRequests.filter(r=>!(r.status===503 && r.url.endsWith('/application-form/save'))),[]);
  assert.equal(report.badRequests.filter(r=>r.status===503).length,1);
  report.passed=true;
 } catch(e){report.failure=e.stack;throw e;}
 finally {write();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
