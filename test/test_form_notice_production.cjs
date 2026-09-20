// 真实生产页面、上传控件、异步解析及隔离数据库；不替换业务方法。
const fs=require('fs'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const base=process.env.AUDIT_BROWSER_URL,output=process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const result={checks:[],errors:[]};page.on('pageerror',e=>result.errors.push(e.message));
 try{
  const ready=await(await fetch(base+'/audit-ready')).json();
  await page.goto(base+'/#/filing-change-review/session/'+ready.project_id);
  await page.waitForFunction(()=>!!document.querySelector('.page-shell')?.__vue__?.project?.project_name);
  const upload=async name=>{
   const bytes=Buffer.from(await(await fetch(base+'/fixture/'+name)).arrayBuffer());
   const old=await page.evaluate(()=>document.querySelector('.page-shell').__vue__.filingParseTaskStates['application-form']?.task_id);
   await page.locator('#pane-form input[type=file]').first().setInputFiles({name,mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:bytes});
   await page.waitForFunction(old=>{const v=document.querySelector('.page-shell').__vue__,s=v.filingParseTaskStates['application-form'];return s?.task_id&&s.task_id!==old&&!v.importingForm;},old,{timeout:120000});
  };
  await upload('old.docx');
  const before=await page.evaluate(()=>document.querySelector('.page-shell').__vue__.formSchema);
  await upload('blank.docx');
  assert.equal(await page.locator('.page-shell > .parse-summary').count(),0);
  assert.equal(await page.locator('#pane-form > .parse-summary').count(),1);
  assert.match(await page.locator('#pane-form > .parse-summary').innerText(),/失败/);
  const after=await page.evaluate(()=>document.querySelector('.page-shell').__vue__.formSchema);
  fs.writeFileSync(output+'/form-comparison.json',JSON.stringify({before,after},null,2));
  // 最新尝试元信息应该从成功变为失败；此前有效字段及来源必须原样保留。
  const {latest_task_attempts:oldAttempt,...oldEffective}=before;
  const {latest_task_attempts:newAttempt,...newEffective}=after;
  assert.deepEqual(newEffective,oldEffective);
  assert.equal(newAttempt.application_form.status,'failed');
  assert.ok(await page.locator('.el-message--error').count()<=1);
  result.checks.push('blank replacement: one persistent failure notice, previous effective form retained');
  await page.locator('#pane-form > .parse-summary').getByRole('button',{name:'查看详情',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.page-shell').__vue__.parseDetailGroups.length>0);
  result.checks.push('failure details remain accessible');
  await page.reload();await page.waitForFunction(()=>document.querySelector('.page-shell')?.__vue__?.currentFormParseState?.status==='failed');
  assert.equal(await page.locator('.page-shell > .parse-summary').count(),0);
  assert.equal(await page.locator('#pane-form > .parse-summary').count(),1);
  assert.equal(await page.locator('.el-message--error').count(),0);
  result.checks.push('reload restores single failure notice without repeat toast');
  await upload('new.docx');
  assert.doesNotMatch(await page.locator('#pane-form > .parse-summary').innerText(),/本次解析失败/);
  assert.equal(await page.locator('#pane-form > .parse-summary').count(),1);
  result.checks.push('successful retry replaces old failure notice');
  assert.deepEqual(result.errors,[]);result.status='passed';
  await page.screenshot({path:output+'/notice.png',fullPage:true});
 }catch(e){result.status='failed';result.error=String(e.stack);await page.screenshot({path:output+'/failure.png',fullPage:true});process.exitCode=1;}
 finally{fs.writeFileSync(output+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close();}
})();
