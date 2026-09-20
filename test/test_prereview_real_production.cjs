const fs = require('fs'), assert = require('assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const base = process.env.AUDIT_BROWSER_URL, output = process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output, {recursive:true});
(async()=>{
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH});
 const page = await browser.newPage({viewport:{width:1500,height:1100}});
 const result = {mode:'production_build_real_controller_database_runtime_tasks_synthetic_model_failure', errors:[], badResponses:[], checks:[]};
 page.on('pageerror',e=>result.errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)result.badResponses.push({url:r.url(),status:r.status()});});
 try {
  const ready = await (await fetch(base+'/audit-ready')).json();
  result.database_kind=ready.database_kind;
  const started = await (await fetch(base+'/audit-start',{method:'POST'})).json();
  let task;
  for(let attempt=0;attempt<30;attempt++) {
    task=(await (await fetch(base+'/api/pre-review/runs/tasks/'+started.task_id+'/progress')).json()).data;
    if(task.status==='failed')break;
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  assert.equal(task.status,'failed');
  const url=base+'/#/pre-review/session/'+ready.project_id;
  await page.goto(url);
  await page.getByText('本次审评未完成，仅展示已保存的部分结果，不能导出完整审评报告。',{exact:true}).waitFor({timeout:45000});
  await page.waitForFunction(()=>!!document.querySelector('.session-page')?.__vue__?.resultOverview,null,{timeout:45000});
  const snapshot=await page.evaluate(()=>{const v=document.querySelector('.session-page').__vue__;return {run:v.selectedRunId,summary:v.selectedRunSummary,overview:v.resultOverview};});
  assert.equal(snapshot.summary.error.code,'model_timeout');
  assert.deepEqual(snapshot.summary.completed_section_ids,['3.2.p.2.1']);
  assert.deepEqual(snapshot.summary.incomplete_section_ids,['3.2.p.2.2']);
  assert.ok(JSON.stringify(snapshot.overview).includes('SAVED_FIRST_CHAPTER'));
  assert.equal(await page.getByRole('button',{name:'导出审评结论',exact:true}).isDisabled(),true);
  result.checks.push('persisted failure and first chapter rendered');result.run_id=snapshot.run;
  for(const suffix of ['export','review-conclusions/export','review-conclusions/download']) {
   const response=await fetch(base+'/api/pre-review/runs/'+snapshot.run+'/'+suffix,{method:suffix.endsWith('download')?'GET':'POST'});
   assert.equal(response.status,400);assert.match((await response.json()).message,/未完成/);
  }
  result.checks.push('all report HTTP paths reject persisted failure');
  await page.reload();
  await page.getByText('失败阶段：section_review',{exact:true}).waitFor({timeout:45000});
  result.checks.push('reload preserves failed selection');
  await page.goto(url+'?run_id=historical-success'); await page.reload();
  await page.waitForFunction(()=>document.querySelector('.session-page')?.__vue__?.selectedRunId==='historical-success',null,{timeout:45000});
  assert.equal(await page.getByRole('button',{name:'导出审评结论',exact:true}).isDisabled(),false);
  result.checks.push('explicit historical successful run accessible');
  const empty = await (await fetch(base+'/audit-start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fail_first:true})})).json();
  const terminal = await page.evaluate(async taskId=>{
    const v=document.querySelector('.session-page').__vue__;
    const context=v.beginMainRunOperation('run',{docId:'synthetic-doc'});
    v.runPollingTaskId=taskId;
    let error='';try{await v.waitForRunTask(taskId,context);}catch(e){error=e.message;}
    return {error,run:v.selectedRunId,summary:v.selectedRunSummary};
  },empty.task_id);
  assert.match(terminal.error,/model_timeout/);
  assert.notEqual(terminal.run,'historical-success');assert.notEqual(terminal.run,snapshot.run);
  assert.equal(terminal.summary.review_complete,false);
  assert.deepEqual(terminal.summary.completed_section_ids,[]);
  assert.equal(await page.getByRole('button',{name:'导出审评结论',exact:true}).isDisabled(),true);
  result.checks.push('actual task polling selects new failure with no partial result');
  assert.deepEqual(result.errors,[]);assert.deepEqual(result.badResponses,[]);result.passed=true;
 } catch(e){result.failure=e.stack;await page.screenshot({path:output+'/failure.png',fullPage:true});throw e;}
 finally{fs.writeFileSync(output+'/result.json',JSON.stringify(result,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
