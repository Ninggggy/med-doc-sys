const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../agent_fronted/src/views/KnowledgeManage.vue'), 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'module.exports =');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a;reject=b; }); return {promise,resolve,reject}; };
function fixture() {
  const reads=[], deletes=[], messages=[];
  const context = {module:{exports:{}},
    listDeletionCleanups: () => { const d=deferred();reads.push(d);return d.promise; },
    deleteKnowledge: () => { const d=deferred();deletes.push(d);return d.promise; }};
  vm.runInNewContext(script, context);
  const p={deletionCleanup:{visible:true,list:[],page:1,total:0,token:0,loading:false},deletionBusy:{},
    $set:(o,k,v)=>o[k]=v,$delete:(o,k)=>delete o[k],$message:{success:m=>messages.push(['success',m]),warning:m=>messages.push(['warning',m]),error:m=>messages.push(['error',m])}};
  for(const [k,v] of Object.entries(context.module.exports.methods))p[k]=v.bind(p);
  return {p,reads,deletes,messages};
}
(async()=>{
  {
    const {p,messages}=fixture();p.showDeletionResult({cleanup_state:'pending'});
    assert.equal(messages[0][0],'warning');p.showDeletionResult({cleanup_state:'completed'});assert.equal(messages[1][0],'success');
  }
  for(const lateError of [false,true]){
    const {p,reads,messages}=fixture();const old=p.loadDeletionCleanups(),current=p.loadDeletionCleanups();
    reads[1].resolve({data:{list:[{doc_id:'new'}],total:1}});await current;
    if(lateError)reads[0].reject(Error('old'));else reads[0].resolve({data:{list:[{doc_id:'old'}],total:1}});
    await old;assert.equal(p.deletionCleanup.list[0].doc_id,'new');assert.equal(messages.length,0);assert.equal(p.deletionCleanup.loading,false);
  }
  {
    const {p,reads,messages}=fixture();p.deletionCleanup.list=[{doc_id:'retained'}];
    const request=p.loadDeletionCleanups();reads[0].reject(Error('synthetic'));await request;
    assert.equal(p.deletionCleanup.list[0].doc_id,'retained');assert.equal(messages[0][0],'error');
  }
  {
    const {p,reads,deletes}=fixture();const first=p.retryDeletionCleanup({doc_id:'A'});await p.retryDeletionCleanup({doc_id:'A'});
    assert.equal(deletes.length,1);deletes[0].resolve({data:{cleanup_state:'completed'}});await new Promise(setImmediate);
    reads[0].resolve({data:{list:[],total:0}});await first;assert.equal(p.deletionBusy.A,undefined);
  }
  {
    const {p,reads,messages}=fixture();const request=p.loadDeletionCleanups();p._isDestroyed=true;
    reads[0].resolve({data:{list:[{doc_id:'late'}],total:1}});await request;
    assert.equal(p.deletionCleanup.list.length,0);assert.equal(messages.length,0);
  }
  console.log('PASS deletion UI: truthful notice, stale success/failure, retained list, duplicate retry, destroyed component');
})().catch(error=>{console.error(error);process.exitCode=1;});
