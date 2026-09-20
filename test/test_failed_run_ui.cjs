const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'),'utf8');
const script=source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','module.exports =');
const calls=[];
const context={module:{exports:{}},MarkdownIt:function(){},StabilityLimitDetails:{},
  listFilingReviewHistory:async()=>({data:{list:[{run_id:'failed-new',status:'failed'},{run_id:'old-ok',status:'completed'}]}}),
  getFilingRunResult:async id=>({data:id==='failed-new'?{run_id:id,execution_status:'failed',review_complete:false,partial_result:{},incomplete_stages:['summary']}:{run_id:id,execution_status:'completed',review_complete:true}}),
  generateFilingRunReport:async()=>calls.push('generate'),manualConfirmFilingRun:async()=>calls.push('confirm'),
  downloadFilingReportWord:async()=>calls.push('export')};
vm.runInNewContext(script,context);const options=context.module.exports;
function fixture(query={}) {
  const p={...options.data(),projectId:'synthetic',$route:{query},$set:(o,k,v)=>o[k]=v};
  for(const [name,method] of Object.entries(options.methods))p[name]=method.bind(p);
  Object.defineProperty(p,'reviewIncomplete',{get:options.computed.reviewIncomplete.bind(p)});
  p.readCurrentData=async(_key,read,apply)=>apply(await read(()=>true));
  return p;
}
(async()=>{
  const p=fixture();await p.loadLatestResult();
  assert.equal(p.reviewResult.run_id,'failed-new');assert.equal(p.reviewIncomplete,true);
  p.projectReport={run_id:'failed-new',report_id:'stale'};
  await p.saveManualOpinion();await p.regenerateReport();await p.exportReportWord();
  assert.deepEqual(calls,[]);
  await p.loadReport();assert.equal(p.projectReport,null);
  const historic=fixture({run_id:'old-ok'});await historic.loadLatestResult();
  assert.equal(historic.reviewResult.run_id,'old-ok');assert.equal(historic.reviewIncomplete,false);
  historic.$route.query={};await historic.loadLatestResult();
  assert.equal(historic.reviewResult.run_id,'old-ok');
  console.log('PASS latest failed attempt, explicit/history selection preservation, failure readonly/report guards');
})().catch(e=>{console.error(e);process.exitCode=1});
