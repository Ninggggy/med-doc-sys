const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'),'utf8');
const script=source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','module.exports =');
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};};
function fixture() {
 const pending=defer(), messages=[],calls=[];
 const c={module:{exports:{}},MarkdownIt:function(){},StabilityLimitDetails:{},manualConfirmFilingRun:(id,payload)=>{calls.push({id,payload});return pending.promise;},getFilingRunResult:async id=>({data:result(id)})};
 vm.runInNewContext(script,c);const options=c.module.exports;
 const p={...options.data(),projectId:'p',$set:(o,k,v)=>o[k]=v,$message:{success:m=>messages.push(m),error:m=>messages.push(m),warning:m=>messages.push(m)},$confirm:async()=>{throw Error('cancel');}};
 for(const [k,v] of Object.entries(options.methods))p[k]=v.bind(p);
 for(const k of ['manualDirty','manualSaveLabel','reviewIncomplete'])if(options.computed[k])Object.defineProperty(p,k,{get:options.computed[k].bind(p)});
 p.loadReport=async()=>{};p.applyRunResult(result('r'));
 return {p,pending,messages,calls};
}
function result(id, comment='saved', revision=1){return {project_id:'p',run_id:id,manual_confirmation:{comment,reviewer:'reviewer',revision}};}
(async()=>{
 {const {p}=fixture();p.manualComment='draft';p.applyRunResult(result('r','server-new',2));assert.equal(p.manualComment,'draft');assert.equal(await p.confirmDiscardForm(),false);let warned=false;p.warnUnsavedForm({preventDefault:()=>warned=true});assert(warned);}
 {const {p}=fixture();p.manualComment='draft';await p.selectRun('other');assert.equal(p.reviewResult.run_id,'r');assert.equal(p.manualComment,'draft');}
 {const {p,pending,calls}=fixture();p.manualComment='submitted';const saving=p.saveManualOpinion();p.manualComment='continued';pending.resolve({data:{manual_confirmation:{comment:'submitted',reviewer:'reviewer',revision:2}}});await saving;assert.equal(p.manualComment,'continued');assert.equal(p.manualDirty,true);assert.equal(calls[0].payload.expected_revision,1);}
 {const {p,pending}=fixture();p.manualComment='draft';const saving=p.saveManualOpinion();pending.reject(Error('版本冲突'));await saving;assert.equal(p.manualComment,'draft');assert.equal(p.manualDirty,true);assert(p.manualSaveLabel.includes('失败'));}
 {const {p,pending,messages}=fixture();p.manualComment='draft';const saving=p.saveManualOpinion();p.projectId='new-project';p.applyRunResult({...result('new'),project_id:'new-project'},{discardManual:true});pending.resolve({data:{manual_confirmation:{comment:'late',revision:2}}});await saving;assert.equal(p.manualComment,'saved');assert.equal(messages.length,0);}
 console.log('PASS manual opinions: same-run refresh, leave/refresh cancel, run-switch cancel, continued editing, save conflict, stale project response');
})().catch(e=>{console.error(e);process.exitCode=1});
