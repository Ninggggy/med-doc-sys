const fs = require('fs'), assert = require('assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const base = process.env.AUDIT_BROWSER_URL, output = process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output, {recursive:true});
(async()=>{
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH});
 const page = await browser.newPage({viewport:{width:1500,height:1100}});
 const result={mode:'production UI + actual controllers + isolated MySQL; vector fail-once injection',checks:[],errors:[]};
 page.on('pageerror',e=>result.errors.push(e.message));
 const state=async()=>await (await fetch(base+'/audit-cleanup-state')).json();
 try {
  await page.goto(base+'/#/knowledge/guideline');
  const row=page.locator('.el-table__row').filter({hasText:'cleanup-active.txt'});
  await row.getByRole('button',{name:'删除',exact:true}).click();
  await page.locator('.el-message-box').getByRole('button',{name:'确定',exact:true}).click();
  await page.locator('.el-message--warning').filter({hasText:'资料已停用'}).waitFor();
  assert.equal((await state()).pending.total,22);
  assert.equal((await state()).active_original_exists,false);
  result.checks.push('delete committed; injected vector failure shown as pending warning');
  await page.getByRole('button',{name:'删除清理记录',exact:true}).click();
  let dialog=page.locator('.el-dialog').filter({hasText:'待清理记录（全部知识类别）'});
  await dialog.getByText('共 22 条',{exact:true}).waitFor();
  assert.equal(await dialog.locator('.el-table__body-wrapper .el-table__row').count(),20);
  await dialog.locator('.el-pager li').filter({hasText:/^2$/}).click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.el-dialog')].some(d=>d.textContent.includes('待清理记录（全部知识类别）')&&d.querySelectorAll('.el-table__body-wrapper .el-table__row').length===2));
  result.checks.push('22 records accessible via 20 + 2 pagination');
  await page.reload();
  await page.getByRole('button',{name:'删除清理记录',exact:true}).click();
  dialog=page.locator('.el-dialog').filter({hasText:'待清理记录（全部知识类别）'});
  await dialog.getByText('共 22 条',{exact:true}).waitFor();
  assert.equal(await page.locator('.page-shell > .el-card .el-table__row').filter({hasText:'cleanup-active.txt'}).count(),0);
  result.checks.push('reload retains pending records; deleted source absent from active query');
  // Locate active record on either page through rendered rows, without replacing API responses.
  let pending=dialog.locator('.el-table__body-wrapper .el-table__row').filter({hasText:'cleanup-active.txt'});
  if(await pending.count()===0){await dialog.locator('.el-pager li').filter({hasText:/^2$/}).click();await pending.waitFor();}
  await pending.getByRole('button',{name:'重试清理',exact:true}).click();
  await page.locator('.el-message--success').filter({hasText:'相关文件和索引已清理'}).waitFor();
  await dialog.getByText('共 21 条',{exact:true}).waitFor();
  const after=await state();assert.equal(after.pending.total,21);assert.equal(after.calls['cleanup-active'],2);
  assert.ok(!after.pending.list.some(r=>r.doc_id==='cleanup-active'));
  result.checks.push('retry completes once, removes pending row and refreshes total');
  assert.deepEqual(result.errors,[]);
  await page.screenshot({path:output+'/cleanup.png',fullPage:true});
  result.status='passed';
 }catch(error){result.status='failed';result.error=String(error.stack);await page.screenshot({path:output+'/failure.png',fullPage:true});process.exitCode=1;}
 finally{fs.writeFileSync(output+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close();}
})();
