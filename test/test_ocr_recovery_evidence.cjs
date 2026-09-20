const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(path.join(__dirname,'../agent_fronted/src/utils/ocrRecoveryEvidence.js'),'utf8');
const sandbox={}; vm.runInNewContext(source.replace('export function','function')+'\nthis.read=recoveryEvidence;',sandbox);
const chunk={words:[{text:'原值',bbox:[10,10,30,30]}],ocr_recovery:Array.from({length:25},(_,i)=>({
  trigger:'ocr_quality',quality_bbox_pdf:[10,10,30,30],status:i===24?'not_attempted':'unresolved',
  reason_code:i===24?'recovery_region_limit':'recovery_conflict',candidates:i===24?[]:[{text:'候选值'}]
}))};
const before=JSON.stringify(chunk),rows=sandbox.read(chunk,'ocr_quality');
assert.equal(rows.length,25);assert.equal(rows[0].primary,'原值');assert.equal(rows[0].candidates,'候选值');
assert.equal(rows[24].status,'未处理');assert(rows[24].reason.includes('上限'));assert.equal(rows[24].candidates,'');
assert.equal(sandbox.read(chunk,'ocr_coverage').length,0);assert.equal(sandbox.read(chunk,'numeric_uncertain').length,0);
rows[0].box[0]=999;assert.equal(JSON.stringify(chunk),before);
const legacy=sandbox.read({ocr_recovery:[{bbox_pdf:[0,0,2,2],status:'unresolved',reused_attempt_index:0}]},'ocr_coverage');
assert.equal(legacy.length,1);assert.equal(legacy[0].reused,true);
assert.equal(sandbox.read({ocr_recovery:[{bbox_pdf:[0,0,NaN,2]}]},'ocr_coverage')[0].box,null);
console.log('OCR复核证据：25项完整保留、类型隔离、未知/历史兼容及原证据不变通过');
const reused={words:[null],ocr_recovery:[
 {trigger:'ocr_coverage',bbox_pdf:[0,0,100,100],candidates:[{text:'完整块'}]},
 null,
 {trigger:'ocr_quality',bbox_pdf:[10,10,20,20],candidate_scope:'issue_region',reused_attempt_index:0,candidates:[null,{text:'局部'}],
  ambiguous_cell_candidates:[{text:'跨格',candidate_cell_bboxes:[[0,0,40,60],[40,0,100,60],null]}]}
]};
const original=JSON.stringify(reused),mapped=sandbox.read(reused,'ocr_quality');
assert.equal(mapped.length,1);assert.equal(mapped[0].sharedCandidates,'完整块');assert.equal(mapped[0].candidates,'局部');
assert.equal(mapped[0].cellCandidates[0].boxes.length,2);
mapped[0].sharedBox[0]=999;mapped[0].cellCandidates[0].boxes[0][0]=999;assert.equal(JSON.stringify(reused),original);
assert.equal(sandbox.read(null,'ocr_quality').length,0);
reused.ocr_recovery[2].reused_attempt_index=2;assert.equal(sandbox.read(reused,'ocr_quality')[0].sharedCandidates,'');
console.log('共享识别块跨类型可读、候选格完整、非法索引/空历史项及只读保护通过');
const fragments={ocr_recovery:[{status:'unresolved',reason_code:'recovery_reflow_review_required',
  candidates:[{text:'跨行词',source_bboxes_pdf:[[10,10,20,20],[10,40,20,50]]},
    {text:'待定位',source_bboxes_pdf:[null]},{text:'旧格式'}]}]};
const fragmentBefore=JSON.stringify(fragments),fragmentRow=sandbox.read(fragments,'ocr_coverage')[0];
assert.equal(fragmentRow.status,'待核对');assert(fragmentRow.reason.includes('未写入正文'));
assert.equal(fragmentRow.fragmentCandidates.length,2);
assert.equal(fragmentRow.fragmentCandidates[0].boxes.length,2);
assert.equal(fragmentRow.fragmentCandidates[1].incomplete,true);
assert.equal(fragmentRow.fragmentCandidates[1].boxes.length,0);
fragmentRow.fragmentCandidates[0].boxes[0][0]=999;
assert.equal(JSON.stringify(fragments),fragmentBefore);
console.log('重排候选分离定位、非法来源提示与原始证据只读保护通过');
const embedded={embedded_ocr_evidence:[{bbox_pdf:[1,2,30,40],trigger:'ocr_quality',
  status:'conflict',embedded_text:'旧错字',primary_text:'当前字',recovery_candidates:[{text:'复核字'}]}]};
const embeddedBefore=JSON.stringify(embedded),embeddedRows=sandbox.read(embedded,'ocr_quality');
assert.equal(embeddedRows[0].embedded,'旧错字');assert.equal(embeddedRows[0].primary,'当前字');
assert.equal(embeddedRows[0].candidates,'复核字');assert.equal(embeddedRows[0].status,'新旧文字冲突');
embeddedRows[0].box[0]=999;assert.equal(JSON.stringify(embedded),embeddedBefore);
assert.equal(sandbox.read(embedded,'ocr_coverage').length,0);
console.log('旧层、当前与局部复核三方证据及原图定位只读保护通过');
const crossing={embedded_ocr_evidence:[{bbox_pdf:[0,0,40,20],trigger:'ocr_coverage',status:'missing',
  embedded_text:'格内原候选',primary_text:'',ambiguous_primary_words:[{text:'跨两格候选'}]}]};
const crossingBefore=JSON.stringify(crossing);
assert(sandbox.read(crossing,'ocr_coverage')[0].primary.includes('跨格未采用候选：跨两格候选'));
assert.equal(JSON.stringify(crossing),crossingBefore);
