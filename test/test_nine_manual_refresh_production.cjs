const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const seed=JSON.parse(fs.readFileSync(process.env.AUDIT_RUN_SEED));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),report={checks:[],errors:[]};
 page.on('pageerror',e=>report.errors.push(e.message));
 try{
  await page.goto(`http://127.0.0.1:18867/#/filing-change-review/session/${seed.project_id}?run_id=${seed.first_run}`);
  await page.waitForFunction(id=>document.querySelector('.page-shell')?.__vue__?.reviewResult?.run_id===id,seed.first_run);
  await page.getByRole('tab',{name:'AI 审评结果',exact:true}).click();
  const input=page.getByPlaceholder('人工意见与AI结论分别保存');await input.fill('同轮刷新时保留的合成草稿');
  await page.getByRole('tab',{name:'运行记录',exact:true}).click();
  const read=page.waitForResponse(r=>r.url().endsWith(`/runs/${seed.first_run}/result`)&&r.status()===200);
  await page.locator('.el-table__row').filter({hasText:seed.first_run}).getByRole('button',{name:'查看本轮'}).click();
  await read;await page.waitForFunction(()=>document.querySelector('.page-shell').__vue__.activeTab==='result');
  assert.equal(await input.inputValue(),'同轮刷新时保留的合成草稿');report.checks.push('同轮真实接口重新读取不覆盖未保存意见');
  let prompt=false;page.once('dialog',async d=>{assert.equal(d.type(),'beforeunload');prompt=true;await d.dismiss();});
  await page.reload({timeout:10000}).catch(e=>{if(!prompt)throw e;});
  assert.equal(prompt,true);assert.equal(await input.inputValue(),'同轮刷新时保留的合成草稿');
  report.checks.push('浏览器刷新出现beforeunload，取消后草稿保持');
  await page.getByText('药品备案变更类审评',{exact:true}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'取消',exact:true}).click();
  assert.ok(page.url().includes(seed.project_id));assert.equal(await input.inputValue(),'同轮刷新时保留的合成草稿');
  report.checks.push('人工意见未保存时离开页面确认，取消保留');
  assert.deepEqual(report.errors,[]);report.passed=true;
 }catch(e){report.passed=false;report.error=e.stack;process.exitCode=1;}
 finally{fs.writeFileSync(path.join(process.env.AUDIT_BROWSER_OUTPUT,'manual-refresh-production.json'),JSON.stringify(report,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
