const fs=require('fs'), assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const base=process.env.AUDIT_BROWSER_URL||'http://127.0.0.1:18868', pid=process.env.NUMERIC_PROJECT_ID, output=process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1500,height:1100}});
 const result={mode:'production_build_real_HTTP_SQLite_deterministic_review_no_model',errors:[],checks:[]};
 page.on('pageerror',e=>result.errors.push(e.message));
 try{
  await page.goto(`${base}/#/filing-change-review/session/${pid}`);
  await page.getByRole('button',{name:'启动 AI 审评',exact:true}).waitFor();
  async function run(previous){
   await page.getByRole('button',{name:'启动 AI 审评',exact:true}).click();
   await page.waitForFunction(previous=>{
    const v=document.querySelector('.page-shell')?.__vue__;
    return v?.reviewResult?.run_id && v.reviewResult.run_id!==previous && !v.running;
   },previous,{timeout:120000});
   return page.evaluate(()=>{const v=document.querySelector('.page-shell').__vue__;return {id:v.reviewResult.run_id,complete:v.reviewResult.review_complete,limit:v.reviewResult.stability_trend_analysis?.limit_check};});
  }
  const before=await run('');assert.equal(before.limit.undecidable_count,1);result.before=before;
  await page.getByRole('tab',{name:'申报资料',exact:true}).click();
  await page.getByRole('button',{name:'查看解析结果',exact:true}).first().click();
  const dialog=page.locator('.el-dialog:visible');await dialog.waitFor();
  const [download]=await Promise.all([page.waitForEvent('download'),dialog.getByRole('button',{name:'下载原件核对',exact:true}).click()]);
  assert.equal(fs.readFileSync(await download.path(),'utf8'),'synthetic OCR evidence');
  const row=dialog.locator('.el-table__body tbody tr').first();
  await row.locator('input[type=text]').nth(0).fill('-1.25');
  await row.locator('input[type=text]').nth(1).fill('已对照原始单元格及负号');
  await row.locator('.el-checkbox').click();
  await dialog.getByRole('button',{name:'保存人工确认',exact:true}).click();
  await page.waitForFunction(()=>{const v=document.querySelector('.page-shell').__vue__;return v.numericReview.revision===1&&!v.savingNumeric&&!v.numericDirty;});
  await page.screenshot({path:output+'/numeric-confirmed.png',fullPage:true});
  await dialog.locator('.el-dialog__headerbtn').click();
  const after=await run(before.id);assert.equal(after.limit.undecidable_count,0);assert.equal(after.limit.out_of_spec_count,1);result.after=after;
  const old=await page.request.get(`${base}/api/filing-change-review/runs/${before.id}/result`);
  assert(old.ok());assert.equal((await old.json()).data.stability_trend_analysis.limit_check.undecidable_count,1);
  const report=await page.request.get(`${base}/api/filing-change-review/projects/${pid}/report?run_id=${after.id}`);
  assert(report.ok());const reportData=(await report.json()).data;
  assert.equal(reportData.run_id,after.id);
  assert(reportData.report_content.includes('人工数值确认'));
  assert(reportData.report_content.includes('已对照原始单元格及负号'));
  assert(reportData.report_content.includes('manual_confirmed'));
  const exported=await page.request.get(`${base}/api/filing-change-review/reports/${reportData.report_id}/export-word`);
  assert(exported.ok());assert.equal((await exported.body()).subarray(0,4).toString('hex'),'504b0304');
  const oldReport=await page.request.get(`${base}/api/filing-change-review/projects/${pid}/report?run_id=${before.id}`);
  assert(oldReport.ok());assert(!(await oldReport.json()).data.report_content.includes('manual_confirmed'));
  await page.reload();await page.getByRole('tab',{name:'申报资料',exact:true}).click();
  await page.getByRole('button',{name:'查看解析结果',exact:true}).first().click();
  await page.waitForFunction(()=>document.querySelector('.page-shell')?.__vue__?.numericReview?.revision===1);
  assert.equal(await page.locator('.el-dialog:visible .el-table__body input[type=text]').first().inputValue(),'-1.25');
  result.checks=['unconfirmed excluded','original download exact','manual save','new analysis uses confirmed value','historical result unchanged','new report contains confirmation source','Word export','historical report unchanged','refresh restores confirmation'];
  assert.equal(result.errors.length,0);result.passed=true;
 }catch(error){result.failure=error.stack;await page.screenshot({path:output+'/numeric-failure.png',fullPage:true});throw error;}
 finally{fs.writeFileSync(output+'/numeric-production-result.json',JSON.stringify(result,null,2));await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
