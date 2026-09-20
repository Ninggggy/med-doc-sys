// Production build + real isolated backend; save failure/delay injected at browser transport.
const fs=require('fs'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const base=process.env.AUDIT_BROWSER_URL,output=process.env.AUDIT_BROWSER_OUTPUT;
const seed={project_id:'fcrp_d2a960ae358b4e0b',run_id:'fcrr_932cf43396604cbd'};
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const report={mode:'production_build_real_SQLite_save_failure_and_delay_transport_injection',checks:[],errors:[]};
 const draft='合成草稿：失败与跨项目保护 '+Date.now();
 page.on('pageerror',e=>report.errors.push(e.message));
 try{
  const create=await fetch(base+'/api/filing-change-review/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_name:'隔离人工意见跨项目验收'})});
  assert.equal(create.status,200);const target=(await create.json()).data.project_id;
  await page.goto(base+'/#/filing-change-review/session/'+seed.project_id+'?run_id='+seed.run_id);
  await page.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.reviewResult?.run_id===id,seed.run_id,{timeout:60000});
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  const input=page.getByPlaceholder('人工意见与AI结论分别保存');
  await input.fill(draft);
  const endpoint='**/runs/'+seed.run_id+'/manual-confirm';
  await page.route(endpoint,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:503,message:'合成保存失败'})}));
  await page.getByRole('button',{name:'保存本轮人工意见',exact:true}).click();
  await page.getByText(/^意见状态：保存失败/).waitFor();
  assert.equal(await input.inputValue(),draft);
  await page.unroute(endpoint);report.checks.push('save 503 preserves input and failure status');
  const targetPath='/filing-change-review/session/'+target;
  await page.evaluate(path=>{document.querySelector('.page-shell').__vue__.$router.push(path).catch(()=>{});},targetPath);
  await page.locator('.el-message-box').getByRole('button',{name:'取消',exact:true}).click();
  assert.ok(page.url().includes(seed.project_id));assert.equal(await input.inputValue(),draft);
  report.checks.push('cross-project navigation cancellation preserves draft and selection');
  let release,received;
  const arrived=new Promise(resolve=>received=resolve), gate=new Promise(resolve=>release=resolve);
  await page.route(endpoint,async route=>{const response=await route.fetch();received();await gate;await route.fulfill({response});});
  await page.evaluate(async ()=>{
    const v=document.querySelector('.page-shell').__vue__, original=v.saveManualOpinion;
    window.__auditSaveDone=false;
    v.saveManualOpinion=async function(){try{return await original.call(this);}finally{window.__auditSaveDone=true;}};
    v.$forceUpdate();await v.$nextTick();
  });
  await page.getByRole('button',{name:'保存本轮人工意见',exact:true}).click();await arrived;
  await page.evaluate(path=>{document.querySelector('.page-shell').__vue__.$router.push(path).catch(()=>{});},targetPath);
  await page.locator('.el-message-box').getByRole('button',{name:'确定',exact:true}).click();
  await page.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.projectId===id,target);
  release();
  await page.waitForFunction(()=>window.__auditSaveDone===true);
  const state=await page.evaluate(()=>{const v=document.querySelector('.page-shell').__vue__;return {project:v.projectId,run:v.reviewResult?.run_id,dirty:v.manualDirty,comment:v.manualComment,reviewer:v.manualReviewer};});
  assert.equal(state.project,target);assert.ok(!state.run);assert.equal(state.dirty,false);
  assert.ok(!JSON.stringify(state).includes('合成草稿'));report.checks.push('late real save response cannot populate another project');
  await page.unroute(endpoint);
  await page.evaluate(path=>{document.querySelector('.page-shell').__vue__.$router.push(path).catch(()=>{});},'/filing-change-review/session/'+seed.project_id+'?run_id='+seed.run_id);
  await page.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.reviewResult?.run_id===id,seed.run_id);
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  await input.fill('合成草稿：组件销毁保护 '+Date.now());
  let releaseDestroyed,receivedDestroyed;
  const arrivedDestroyed=new Promise(resolve=>receivedDestroyed=resolve),gateDestroyed=new Promise(resolve=>releaseDestroyed=resolve);
  await page.route(endpoint,async route=>{const response=await route.fetch();receivedDestroyed();await gateDestroyed;await route.fulfill({response});});
  await page.evaluate(async ()=>{
    const v=document.querySelector('.page-shell').__vue__,original=v.saveManualOpinion;
    window.__auditSaveDone=false;window.__auditOldVm=v;
    v.saveManualOpinion=async function(){try{return await original.call(this);}finally{window.__auditSaveDone=true;}};
    v.$forceUpdate();await v.$nextTick();
  });
  await page.getByRole('button',{name:'保存本轮人工意见',exact:true}).click();await arrivedDestroyed;
  await page.getByText('药品备案变更类审评',{exact:true}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'确定',exact:true}).click();
  await page.waitForFunction(()=>window.__auditOldVm._isDestroyed===true);
  releaseDestroyed();await page.waitForFunction(()=>window.__auditSaveDone===true);
  assert.equal(await page.evaluate(()=>window.__auditOldVm.pageDisposed),true);
  assert.ok(!page.url().includes('/session/'));
  report.checks.push('late save completes safely after actual component destruction');
  assert.deepEqual(report.errors,[]);report.passed=true;
 }catch(e){report.failure=e.stack;await page.screenshot({path:output+'/failure.png',fullPage:true});throw e;}
 finally{fs.writeFileSync(output+'/result.json',JSON.stringify(report,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
