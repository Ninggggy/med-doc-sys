const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const seed=JSON.parse(fs.readFileSync(process.env.AUDIT_RUN_SEED));
const out=process.env.AUDIT_BROWSER_OUTPUT,base='http://127.0.0.1:18867';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const result={mode:'current_production_build_real_controller_SQLite_synthetic_runs',checks:[],errors:[]};
 page.on('pageerror',e=>result.errors.push(e.message));
 const state=()=>page.evaluate(()=>{const v=document.querySelector('.page-shell').__vue__;return {run:v.reviewResult?.run_id,incomplete:v.reviewIncomplete,dirty:v.manualDirty,revision:v.manualLoaded.revision};});
 const waitRun=id=>page.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.reviewResult?.run_id===id,id,{timeout:60000});
 async function choose(id){await page.getByRole('tab',{name:'运行记录',exact:true}).click();await page.locator('.el-table__row').filter({hasText:id}).getByRole('button',{name:'查看本轮'}).click();await waitRun(id);}
 try{
  await page.goto(`${base}/#/filing-change-review/session/${seed.project_id}`);
  await waitRun(seed.failed_run);assert.equal((await state()).incomplete,true);
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  await page.getByText('本次审评未完成，以下仅为部分结果',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'保存本轮人工意见'}).isDisabled(),true);
  await page.reload();await waitRun(seed.failed_run);
  await page.getByRole('tab',{name:'审评报告',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'生成或重新生成当前轮次报告'}).isDisabled(),true);
  result.checks.push('默认最近失败轮次，刷新保持，人工确认/完整报告禁用');
  await choose(seed.first_run);
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  const input=page.getByPlaceholder('人工意见与AI结论分别保存');
  await input.fill('合成未保存意见');
  await page.getByRole('tab',{name:'运行记录',exact:true}).click();
  await page.locator('.el-table__row').filter({hasText:seed.second_run}).getByRole('button',{name:'查看本轮'}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'取消',exact:true}).click();
  assert.equal((await state()).run,seed.first_run);
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();assert.equal(await input.inputValue(),'合成未保存意见');
  let release,arrived;const hold=new Promise(r=>release=r),pending=new Promise(r=>arrived=r);
  await page.route('**/manual-confirm',async route=>{const response=await route.fetch();arrived();await hold;await route.fulfill({response});});
  await page.getByRole('button',{name:'保存本轮人工意见'}).click();await pending;
  await input.fill('保存期间继续输入');release();
  await page.waitForFunction(()=>!document.querySelector('.page-shell').__vue__.savingManual);
  assert.equal(await input.inputValue(),'保存期间继续输入');assert.equal((await state()).dirty,true);
  await page.unroute('**/manual-confirm');
  await page.getByRole('button',{name:'保存本轮人工意见'}).click();
  await page.waitForFunction(()=>!document.querySelector('.page-shell').__vue__.savingManual);
  assert.equal((await state()).dirty,false);
  await page.reload();await waitRun(seed.first_run);await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  assert.equal(await input.inputValue(),'保存期间继续输入');
  const rev=(await state()).revision;
  const remote=await page.request.post(`${base}/api/filing-change-review/runs/${seed.first_run}/manual-confirm`,{data:{comment:'另一窗口的新意见',reviewer:'合成复核人',expected_revision:rev}});
  assert.equal(remote.status(),200);
  await input.fill('本窗口冲突草稿');await page.getByRole('button',{name:'保存本轮人工意见'}).click();
  await page.waitForFunction(()=>!document.querySelector('.page-shell').__vue__.savingManual);
  assert.equal(await input.inputValue(),'本窗口冲突草稿');assert.equal((await state()).dirty,true);
  await page.getByText(/意见状态：.*失败/).waitFor();
  result.checks.push('真实意见保存、保存期间输入、刷新、轮次切换取消、服务版本冲突保护');
  // 新页面读取服务端同一结果；不替换组件数据或生命周期。
  const limitsPage=await browser.newPage({viewport:{width:1440,height:1050}});
  await limitsPage.goto(`${base}/#/filing-change-review/session/${seed.project_id}?run_id=${seed.second_run}`);
  await limitsPage.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.reviewResult?.run_id===id,seed.second_run);
  await limitsPage.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  const panel=limitsPage.locator('.stability-limit-details');await panel.locator(':scope > summary').click();
  const sections=panel.locator(':scope > section');const counts=[];
  for(let group=0;group<2;group++){
   const section=sections.nth(group),seen=new Set();
   for(let p=0;p<50;p++){
    const names=await section.locator('.limit-entry > b').allTextContents();assert.equal(names.length,20);names.forEach(x=>seen.add(x));
    if(p<49){await section.locator('.btn-next').click();await section.getByText(`当前显示 ${(p+1)*20+1}–${(p+2)*20} / 1000 条`,{exact:true}).waitFor();}
   }
   assert.equal(seen.size,1000);counts.push(seen.size);
  }
  result.exceptionCounts=counts;
  result.paginationMs=await limitsPage.evaluate(async()=>{
   const samples=[];for(let i=0;i<20;i++){
    const s=performance.now();document.querySelectorAll('.stability-limit-details section .btn-prev')[i%2].click();
    await document.querySelector('.page-shell').__vue__.$nextTick();await new Promise(requestAnimationFrame);samples.push(performance.now()-s);
   }return samples;
  });
  result.paginationP95=[...result.paginationMs].sort((a,b)=>a-b)[18];assert.ok(result.paginationP95<=500);
  await panel.screenshot({path:path.join(out,'1000-exceptions.png')});await limitsPage.close();
  result.checks.push('数据库读取各1000条异常，逐页验证2000条唯一明细，分页20次P95');
  assert.deepEqual(result.errors,[]);result.passed=true;
 }catch(e){result.passed=false;result.error=e.stack;process.exitCode=1;}
 finally{fs.writeFileSync(path.join(out,'nine-runs-result.json'),JSON.stringify(result,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
