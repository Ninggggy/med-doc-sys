// 当前Vue真实模板、原方法、真实异步服务/SQLite；进度仅由同步点控制。
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium,request}=require('/tmp/parser-defects-browser/node_modules/playwright');
const root=process.env.F08_BROWSER_ROOT||path.join(__dirname,'f08_f18_completion_20260906');
const inputs=process.env.F08_BROWSER_INPUTS||path.join(root,'browser-inputs');
const base='http://127.0.0.1:'+(process.env.F01_BROWSER_PORT||18769), output=fs.mkdtempSync(path.join(root,'browser-run-'));
const report={cases:[],errors:[],output}; let browser,api,page;
const get=async route=>(await api.get(base+route)).json();
const post=async(route,data={})=>(await api.post(base+route,{data})).json();
async function refresh(p=page){await p.goto(base);await p.waitForFunction(()=>window.fixtureReady===true);}
async function section(label){
 label=({'药品注册申请人':'申请人信息','制剂生产企业':'生产企业信息','委托研究机构':'委托研究机构信息'})[label]||label;
 const n=page.locator('.el-collapse-item').filter({has:page.locator('.el-collapse-item__header').filter({hasText:label})});
 assert.equal(await n.count(),1,label);
 if(!(await n.getAttribute('class')).includes('is-active'))await n.locator('.el-collapse-item__header').click();
 return n;
}
async function sub(label,key){if(key==='电话')key='联系电话';return (await section(label)).locator('.el-form-item').filter({has:page.locator('label').filter({hasText:new RegExp('^'+key+'$')})}).locator('input');}
async function taskDone(p=page){
 await p.waitForFunction(()=>{const s=window.page.filingParseTaskStates['application-form'];return s&&['failed','completed'].includes(s.status)&&!window.page.importingForm;},null,{timeout:60000});
}
async function upload(name,p=page){
 await p.locator('input[type=file]').setInputFiles(path.join(inputs,name));
 await p.waitForFunction(()=>!!window.page.filingParseTaskStates['application-form']?.task_id);
}
async function save(){const r=page.waitForResponse(r=>r.url()===base+'/save');await page.getByRole('button',{name:'保存申请表',exact:true}).click();assert.equal((await r).status(),200);await page.waitForFunction(()=>!window.page.savingForm);}
async function reparse(){await page.getByRole('button',{name:'重新解析申请表',exact:true}).click();await page.waitForFunction(()=>window.page.importingForm);await taskDone();}
async function state(){const a=(await get('/data')).data,b=(await get('/database')).data;assert.deepEqual(a.form_json,b.form_json);return a;}
async function scenario(id,run){
 if(process.env.F08_BROWSER_CASE && id!==process.env.F08_BROWSER_CASE)return;
 try{await run();report.cases.push({id,status:'passed'});}catch(e){report.cases.push({id,status:'failed',error:String(e.stack)});}
 await page.screenshot({path:path.join(output,id+'.png'),fullPage:true});
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(report,null,2));console.log(report.cases.at(-1));
}
(async()=>{
 api=await request.newContext();report.fixture=(await get('/ready')).data;
 if(process.env.F08_BROWSER_CASE)report.selected_case=process.env.F08_BROWSER_CASE;
 browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>report.errors.push(String(e)));
 await post('/fresh');await refresh();
 await scenario('F08-duplicate-two-pages',async()=>{
  const second=await browser.newPage();await refresh(second);await post('/f08/pause');
  try{
   await upload('fields.docx');
   for(let n=0;n<100&&!(await get('/f08/entered')).entered;n++)await new Promise(r=>setTimeout(r,20));
   assert.equal((await get('/f08/entered')).entered,true);
   await upload('fields.docx',second);
   assert.equal(await second.evaluate(()=>window.page.filingParseTaskStates['application-form'].task_id),await page.evaluate(()=>window.page.filingParseTaskStates['application-form'].task_id));
  }finally{await post('/f08/release');await taskDone();await taskDone(second);await second.close();}
  assert.equal((await state()).form_json.item_6_generic_name.value,'本轮字段验证药品');
 });
 await scenario('F12-F16-fields-visible-edit-candidate',async()=>{
  await refresh();
  const fields=[['药品注册申请人','法定代表人','法人甲'],['药品注册申请人','联系人','联系人乙'],['药品注册申请人','注册地址','注册丙址'],['制剂生产企业','注册地址','企业注册戊址'],['制剂生产企业','生产地址','生产己址'],['委托研究机构','研究负责人','负责人辛'],['委托研究机构','地址','研究壬址'],['委托研究机构','电话','01012345678']];
  for(const [label,key,value]of fields){const input=await sub(label,key);assert.equal(await input.inputValue(),value);assert.equal(await input.isVisible(),true);}
  for(const [label,placeholder,value]of [['商品名称','商品名称','明确商品名'],['药品注册分类','注册分类号','2.2'],['受理前药品注册检验','检品编号','ABC123']])assert.equal(await(await section(label)).getByPlaceholder(placeholder,{exact:true}).inputValue(),value);
  assert.equal(await(await section('是否为OTC')).locator('label.is-checked').innerText(),'非处方药');
  for(const label of ['药品注册申请人','制剂生产企业','委托研究机构','药品注册分类','商品名称','受理前药品注册检验'])await(await section(label)).screenshot({path:path.join(output,label+'-visible.png')});
  await(await sub('药品注册申请人','联系人')).fill('人工联系人');await save();await refresh();await reparse();
  assert.equal(await(await sub('药品注册申请人','联系人')).inputValue(),'人工联系人');
  assert.equal(await(await sub('药品注册申请人','法定代表人')).inputValue(),'法人甲');
  const s=await section('药品注册申请人');assert.ok((await s.innerText()).includes('联系'));await s.getByRole('button',{name:'保留人工值',exact:true}).click();await save();await refresh();
  assert.equal((await state()).form_json.item_30_applicant_info.sub_fields.contact,'人工联系人');
 });
 await scenario('F18-table-edit-refresh-candidate',async()=>{
  const s=await section('原/辅料/包材来源');const inputs=s.locator('.el-table__body input');
  assert.deepEqual(await inputs.evaluateAll(xs=>xs.map(x=>x.value)),['乙醇','00123','','甲供应商','包装瓶','','00002','乙供应商']);
  await inputs.nth(3).fill('人工供应商');await save();await refresh();await reparse();
  const current=await section('原/辅料/包材来源');assert.equal(await current.locator('.el-table__body input').nth(3).inputValue(),'人工供应商');
  const data=await state();assert.equal(data.form_json.item_17_material_source.candidates.table_rows.value[0].manufacturer,'甲供应商');
  await current.screenshot({path:path.join(output,'F18-visible-manual-and-candidate.png')});
  await current.getByRole('button',{name:'采用候选值',exact:true}).click();await save();await refresh();
  assert.equal((await state()).form_json.item_17_material_source.table_rows[0].manufacturer,'甲供应商');
 });
 await scenario('F09-interrupt-prune-refresh-retry',async()=>{
  const old=await state();await post('/f08/pause');await page.getByRole('button',{name:'重新解析申请表',exact:true}).click();
  await page.waitForFunction(()=>window.page.importingForm);
  for(let n=0;n<100&&!(await get('/f08/entered')).entered;n++)await new Promise(r=>setTimeout(r,20));
  const recovered=await post('/f09/recover-prune');assert.ok(recovered.removed>0);
  await post('/f08/release');await refresh();
  const current=await state();assert.equal(current.parse_status,'success');assert.equal(current.latest_attempt.content_status,'failed');assert.equal(current.original_file_id,old.original_file_id);
  assert.match(await page.locator('.el-alert').first().innerText(),/失败|中断/);
  await page.screenshot({path:path.join(output,'F09-failed-after-prune.png')});
  await reparse();await refresh();assert.equal((await state()).latest_attempt.content_status,'success');
 });
 await scenario('F11-unresolved-table-visible',async()=>{
  await post('/fresh');await refresh();await upload('uncertain.pdf');await taskDone();await refresh();
  const data=await state();assert.equal(data.parse_status,'partial');assert.ok(data.effective_source.parse_diagnostics.errors.some(e=>e.code==='table_structure_unresolved'));
  assert.match(await page.locator('body').innerText(),/表格结构未恢复/);
 });
 fs.writeFileSync(path.join(output,'final-data.json'),JSON.stringify(await state(),null,2));
 if(process.env.F08_BROWSER_CASE)assert.equal(report.cases.length,1,'必须执行选定场景');
 assert.equal(report.errors.length,0);if(report.cases.some(c=>c.status==='failed'))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(api)await api.dispose();});
