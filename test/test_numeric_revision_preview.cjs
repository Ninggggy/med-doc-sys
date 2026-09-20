// 方法级异常/竞争验证，不代替浏览器验收。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'),'utf8');
const method=source.slice(source.indexOf('    async saveNumericConfirmations()'),source.indexOf('    async removeSubmission('));
let save,read;
const box={module:{exports:{}},confirmFilingSubmissionNumbers:(...a)=>save(...a),getFilingSubmissionParsedMarkdown:(...a)=>read(...a)};
vm.runInNewContext('module.exports={'+method+'}',box);
const wait=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function fixture(){return {projectId:'p',numericDocId:'d',parsedMarkdownRequestToken:1,numericReview:{source_attempt:'a',revision:0},
  numericDraft:[{key:'0:0:0',original_text:'1.2',value:'12',reason:'checked',checked:true}],numericSnapshot:'[]',
  parsedMarkdownContent:'old',parsedRevisionWarning:'',numericSaveError:'',$message:{success(){},warning(){}}};}
(async()=>{
  save=async()=>({data:{revision:1}});read=async()=>({data:{numeric_review:{source_attempt:'a',revision:1},markdown:'saved 12'}});
  let p=fixture();await box.module.exports.saveNumericConfirmations.call(p);assert.equal(p.parsedMarkdownContent,'saved 12');
  let pending=wait();read=()=>pending.promise;p=fixture();let run=box.module.exports.saveNumericConfirmations.call(p);await Promise.resolve();
  p.numericDraft[0].value='13';pending.resolve({data:{numeric_review:{source_attempt:'a',revision:1},markdown:'saved 12'}});await run;
  assert.equal(p.numericDraft[0].value,'13');assert.notEqual(JSON.stringify(p.numericDraft),p.numericSnapshot);assert.equal(p.parsedMarkdownContent,'saved 12');
  read=async()=>{throw Error('network')};p=fixture();await box.module.exports.saveNumericConfirmations.call(p);assert.equal(p.numericReview.revision,1);assert(p.parsedRevisionWarning.includes('已保存'));assert.equal(p.numericSaveError,'');
  pending=wait();read=()=>pending.promise;p=fixture();run=box.module.exports.saveNumericConfirmations.call(p);await Promise.resolve();p.projectId='other';pending.resolve({data:{markdown:'wrong'}});await run;assert.equal(p.parsedMarkdownContent,'old');
  read=async()=>({data:{numeric_review:{source_attempt:'new',revision:1},markdown:'new source'}});p=fixture();await box.module.exports.saveNumericConfirmations.call(p);assert.equal(p.parsedMarkdownContent,'old');assert(p.parsedRevisionWarning.includes('已变化'));
  console.log('PASS 5 groups: saved preview, continued edits, refresh failure, stale project, changed source');
})().catch(e=>{console.error(e);process.exitCode=1;});
