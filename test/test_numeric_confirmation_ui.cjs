const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'), 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'module.exports =');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; };
function fixture() {
  const save=deferred(), source=deferred(), messages=[], downloads=[];
  const context={module:{exports:{}}, MarkdownIt:function(){this.render=x=>x;}, StabilityLimitDetails:{}, ParseReviewPanel:{}, confirmFilingSubmissionNumbers:()=>save.promise,
    getFilingSubmissionParsedMarkdown:async()=>({data:{numeric_review:{source_attempt:'attempt',revision:1},markdown:'confirmed view'}}),
    downloadFilingSubmissionOriginal:()=>source.promise, URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    setTimeout:fn=>fn(), document:{body:{appendChild(){}},createElement:()=>({click(){downloads.push(this.download);},remove(){}})}};
  vm.runInNewContext(script,context);
  const options=context.module.exports;
  const page={...options.data(),projectId:'p',formDirty:false,manualDirty:false,$message:{success:x=>messages.push(x),warning:x=>messages.push(x),error:x=>messages.push(x)},$confirm:()=>Promise.reject(new Error('cancel'))};
  for(const [key,method]of Object.entries(options.methods)) page[key]=method.bind(page);
  Object.defineProperty(page,'numericDirty',{get:()=>options.computed.numericDirty.call(page)});
  page.numericReview={source_attempt:'attempt',revision:0};page.numericDocId='doc';
  page.numericDraft=[{key:'0:0:0',original_text:'1.25',value:'-1.25',reason:'checked source',checked:true}];
  return {page,save,source,messages,downloads};
}
(async()=>{
  {
    const {page,save}=fixture();const pending=page.saveNumericConfirmations();
    page.numericDraft[0].value='-1.26';save.resolve({data:{revision:1}});await pending;
    assert.equal(page.numericReview.revision,1);assert(page.numericDirty);assert.equal(page.numericDraft[0].value,'-1.26');assert(!page.savingNumeric);
  }
  {
    const {page,save}=fixture();const pending=page.saveNumericConfirmations();save.reject({userMessage:'conflict'});await pending;
    assert.equal(page.numericSaveError,'conflict');assert(page.numericDirty);assert.equal(page.numericDraft[0].value,'-1.25');
    let closed=false;await page.closeNumericPreview(()=>closed=true);assert(!closed);
    let warned=false;page.warnUnsavedForm({preventDefault(){warned=true;}});assert(warned);
  }
  {
    const {page,save,messages}=fixture();const pending=page.saveNumericConfirmations();page.projectId='other';save.resolve({data:{revision:9}});await pending;
    assert.equal(page.numericReview.revision,0);assert.equal(messages.length,0);
  }
  {
    const {page,source,downloads}=fixture(); page.numericSourceName='original.pdf';
    const pending=page.downloadNumericSource();source.resolve({});await pending;
    assert.deepEqual(downloads,['original.pdf']);assert(!page.downloadingNumericSource);
  }
  {
    const {page,source,downloads,messages}=fixture();page.numericSourceName='original.pdf';
    const pending=page.downloadNumericSource();page.projectId='other';source.resolve({});await pending;
    assert.equal(downloads.length,0);assert.equal(messages.length,0);
  }
  {
    const {page,source,downloads,messages}=fixture();page.numericSourceName='original.pdf';
    const pending=page.downloadNumericSource();source.reject(new Error('原件已删除'));await pending;
    assert.equal(downloads.length,0);assert(messages.includes('原件已删除'));assert(!page.downloadingNumericSource);
  }
  console.log('PASS numeric confirmation: continued typing, conflict retention, leave/refresh warning, stale project response');
})().catch(error=>{console.error(error);process.exitCode=1;});
