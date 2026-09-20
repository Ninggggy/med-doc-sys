// 实际Vue页面组件、合成结果和保存接口，不冒充生产后端联动。
const fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.join(__dirname,'../agent_fronted');
const source=fs.readFileSync(path.join(root,'src/views/filing-change-review/FilingChangeReviewSession.vue'),'utf8');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try {
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<div id="app"></div>');
  await page.addScriptTag({path:path.join(root,'node_modules/vue/dist/vue.js')});
  await page.addScriptTag({path:path.join(root,'node_modules/element-ui/lib/index.js')});
  await page.addStyleTag({path:path.join(root,'node_modules/element-ui/lib/theme-chalk/index.css')});
  const script=source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','window.component =');
  await page.addScriptTag({content:'window.MarkdownIt=function(){this.render=x=>x};window.StabilityLimitDetails={template:"<div />"};'+script});
  await page.evaluate(template=>{
   component.template=template;delete component.mounted;delete component.watch;
   component.computed.projectId=()=> 'p';
   window.app=new Vue(component).$mount('#app');
   window.result=()=>({project_id:'p',run_id:'r',manual_confirmation:{comment:'saved',reviewer:'reviewer',revision:1}});
   app.applyRunResult(result());app.activeTab='result';
   window.manualConfirmFilingRun=()=>new Promise(resolve=>window.finishSave=resolve);
  },source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1]);
  const input=page.getByPlaceholder('人工意见与AI结论分别保存');
  await input.fill('draft');await page.evaluate(()=>app.applyRunResult(result()));assert.equal(await input.inputValue(),'draft');
  await page.getByRole('button',{name:'保存本轮人工意见',exact:true}).click();
  await input.fill('continued');await page.evaluate(()=>finishSave({data:{manual_confirmation:{comment:'draft',reviewer:'reviewer',revision:2}}}));
  await page.getByText('意见状态：未保存',{exact:true}).waitFor();assert.equal(await input.inputValue(),'continued');
  const leaving=page.evaluate(()=>app.confirmDiscardForm());
  await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(await leaving,false);assert.equal(await input.inputValue(),'continued');
  assert.deepEqual(errors,[]);console.log('PASS real Vue input: refresh preserves draft, editing during save remains dirty, leave cancellation preserves input');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
