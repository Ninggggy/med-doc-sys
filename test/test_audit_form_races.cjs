const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'), 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'module.exports =');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const tick = () => new Promise(setImmediate);
function fixture() {
  const forms=[], details=[], saves=[], errors=[];
  const context={module:{exports:{}}, StabilityLimitDetails:{}, MarkdownIt:function(){this.render=x=>x},
    getFilingChangeProjectDetail:()=>{const d=deferred();details.push(d);return d.promise;},
    getApplicationForm:()=>{const d=deferred();forms.push(d);return d.promise;},
    saveApplicationForm:()=>{const d=deferred();saves.push(d);return d.promise;}};
  vm.runInNewContext(script,context);
  const c=context.module.exports;
  const page={...c.data(),projectId:'A',$set:(o,k,v)=>o[k]=v,$delete:(o,k)=>delete o[k],
    $message:{error:m=>errors.push(m),success:()=>{}},$confirm:async()=>{},$nextTick:f=>f(),$refs:{}};
  Object.entries(c.methods).forEach(([k,v])=>page[k]=v.bind(page));
  for (const k of ['restoreParseTasks','loadSubmissionCatalog','loadSubmissions','loadReferenceTaxonomy','loadLatestResult','loadRunHistory','loadReferenceMaterials','loadRules','loadReport']) page[k]=async()=>{};
  const response=value=>({data:{form_json:{field:{field_type:'text',value}},effective_source:{name:value},parse_status:'success'}});
  return {page,forms,details,saves,errors,response};
}
(async()=>{
  for(const scenario of ['late-success','late-error','project-switch','disposed']){
    const f=fixture(), p=f.page;
    const first=p.loadBaseData();await tick();
    if(scenario.startsWith('late')){
      const second=p.loadBaseData();await tick();
      f.details[1].resolve({data:{name:'new'}});f.forms[1].resolve(f.response('new'));await second;
    }else if(scenario==='project-switch')p.projectId='B';else p.pageDisposed=true;
    f.details[0].resolve({data:{name:'old'}});
    if(scenario==='late-error')f.forms[0].reject(new Error('old failure'));else f.forms[0].resolve(f.response('old'));
    await first;
    assert.notEqual(p.formSource.name,'old');assert.equal(f.errors.length,0);
    console.log('PASS',scenario);
  }
  {
    const f=fixture(),p=f.page;p.applyFormSchema(f.response('initial').data.form_json);
    p.formSchema.field.value='edit';p.markFormEdited(p.formSchema.field,'value');
    const read=p.loadBaseData();await tick();f.details[0].resolve({data:{}});f.forms[0].resolve(f.response('server'));await read;
    assert.equal(p.formSchema.field.value,'edit');assert.equal(p.formDirty,true);
    const save=p.saveForm();p.formSchema.field.value='newer';p.markFormEdited(p.formSchema.field,'value');
    f.saves[0].resolve(f.response('edit'));await save;
    assert.equal(p.formSchema.field.value,'newer');assert.equal(p.formDirty,true);
    const fail=p.saveForm();f.saves[1].reject(new Error('synthetic failure'));assert.equal(await fail,false);
    assert.equal(p.formSchema.field.value,'newer');assert.equal(p.formDirty,true);
    p.$confirm=async()=>{throw Error('cancel');};assert.equal(await p.confirmDiscardForm(),false);
    let prevented=false;const e={preventDefault:()=>prevented=true};p.warnUnsavedForm(e);assert.equal(prevented,true);
    console.log('PASS editing refresh/save/failure/navigation');
  }
  {
    const f=fixture(),p=f.page;p.applyFormSchema(f.response('initial').data.form_json);
    const save=p.saveForm();p.projectId='B';f.saves[0].resolve(f.response('old project'));
    assert.equal(await save,false);assert.equal(p.formSchema.field.value,'initial');
    console.log('PASS stale save ignored');
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
